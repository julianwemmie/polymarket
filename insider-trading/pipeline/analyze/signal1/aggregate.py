# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0.0"]
# ///
"""
Script 10: Aggregate Score

Reads all individual metric parquet files, normalizes each metric to a
0-1 scale using percentile ranks, applies configurable weights, and
produces a final ranked list of the most statistically implausible wallets.

Each metric is mapped to a single suspicion score:
  - contrarian_win_rate:  contrarian_win_rate (higher = more suspicious)
  - niche_market_accuracy: niche_win_rate (higher = more suspicious)
  - profit_factor:        profit_factor (higher = more suspicious, capped)
  - brier_score:          brier_skill_vs_consensus (higher = more suspicious)
  - position_concentration: max_concentration (higher = more suspicious)
  - win_streak:           longest_win_streak (higher = more suspicious)
  - roi:                  roi (higher = more suspicious)
  - bet_size_vs_odds:     longshot_win_rate

The final score is a weighted percentile rank across all metrics.

Minimum metric coverage: wallets must have data for at least 3 metrics
to receive a final score. Wallets below this threshold are excluded.

Output: output/aggregate_scores.parquet
"""
import os
from pathlib import Path
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
DATA_ROOT = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("POLYMARKET_OUTPUT_DIR", str(DATA_ROOT / "analyze" / "signal1")))
OUTPUT_PATH = OUTPUT_DIR / "aggregate_scores.parquet"

# Minimum resolved bets to include a wallet in the aggregate
MIN_RESOLVED_BETS = 5

# Minimum number of metrics a wallet must have to receive a final score
MIN_METRIC_COVERAGE = 3

# Minimum number of wallets (non-null) required for a metric to be included
# in the percentile computation. Metrics with fewer wallets inflate scores
# because top-of-100 gets the same 1.0 percentile as top-of-100,000.
MIN_METRIC_POPULATION = 50

# Weights for each metric (must sum to 1.0)
WEIGHTS = {
    "contrarian_win_rate": 0.15,
    "niche_market_accuracy": 0.10,
    "profit_factor": 0.15,
    "brier_score": 0.15,
    "position_concentration": 0.10,
    "win_streak": 0.10,
    "roi": 0.15,
    "bet_size_vs_odds": 0.10,
}

# Input files and their key metric columns
METRIC_FILES = {
    "contrarian_win_rate": {
        "path": OUTPUT_DIR / "contrarian_win_rate.parquet",
        "metric_col": "contrarian_win_rate",
        "higher_is_suspicious": True,
        "bet_count_col": "contrarian_bet_count",
    },
    "niche_market_accuracy": {
        "path": OUTPUT_DIR / "niche_market_accuracy.parquet",
        "metric_col": "niche_win_rate",
        "higher_is_suspicious": True,
        "bet_count_col": "niche_bet_count",
    },
    "profit_factor": {
        "path": OUTPUT_DIR / "profit_factor.parquet",
        "metric_col": "profit_factor",
        "higher_is_suspicious": True,
        "bet_count_col": "resolved_bet_count",
    },
    "brier_score": {
        "path": OUTPUT_DIR / "brier_score.parquet",
        # Use relative skill vs consensus (higher = more suspicious = better than market)
        "metric_col": "brier_skill_vs_consensus",
        "higher_is_suspicious": True,
        "bet_count_col": "resolved_bet_count",
    },
    "position_concentration": {
        "path": OUTPUT_DIR / "position_concentration.parquet",
        "metric_col": "max_concentration",
        "higher_is_suspicious": True,
        "bet_count_col": "total_positions",
    },
    "win_streak": {
        "path": OUTPUT_DIR / "win_streak.parquet",
        "metric_col": "longest_win_streak",
        "higher_is_suspicious": True,
        "bet_count_col": "resolved_bet_count",
    },
    "roi": {
        "path": OUTPUT_DIR / "roi.parquet",
        "metric_col": "roi",
        "higher_is_suspicious": True,
        "bet_count_col": "resolved_bet_count",
    },
    "bet_size_vs_odds": {
        "path": OUTPUT_DIR / "bet_size_vs_odds.parquet",
        # Primary suspicion signal: longshot win rate.
        # NOTE: This only scores wallets with longshot bets. Extreme-favorite
        # stats are kept for informational purposes in bet_size_vs_odds.parquet
        # but are not used as a flag (insiders short favorites by buying the
        # underdog, which is captured by the longshot analysis).
        "metric_col": "longshot_win_rate",
        "higher_is_suspicious": True,
        "bet_count_col": "longshot_bet_count",
    },
}


def main():
    print("=" * 60)
    print("Script 10: Aggregate Score")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-6, (
        f"Weights must sum to 1.0, got {sum(WEIGHTS.values())}"
    )

    # Load each metric and extract wallet + normalized score
    loaded_metrics = {}
    for name, config in METRIC_FILES.items():
        path = config["path"]
        if not path.exists():
            print(f"  WARNING: {name} not found at {path}, skipping")
            continue

        print(f"  Loading {name}...")
        df = pl.read_parquet(path)

        metric_col = config["metric_col"]
        bet_count_col = config["bet_count_col"]

        if len(df) == 0 or metric_col not in df.columns:
            print(f"    -> 0 wallets with {name} (empty or missing columns)")
            continue

        # Select wallet, metric value, and bet count
        selected = df.select(
            "wallet",
            pl.col(metric_col).alias(f"raw_{name}"),
            pl.col(bet_count_col).alias(f"num_bets_{name}"),
        ).filter(
            # Only include wallets with enough data for this metric
            pl.col(f"raw_{name}").is_not_null()
            & pl.col(f"num_bets_{name}").is_not_null()
            & (pl.col(f"num_bets_{name}") >= MIN_RESOLVED_BETS)
        )

        # Cap extreme values (profit_factor, roi can be huge)
        if name in ("profit_factor", "roi"):
            cap_val = selected.select(pl.col(f"raw_{name}").quantile(0.99)).item()
            if cap_val is not None and cap_val > 0:
                selected = selected.with_columns(
                    pl.col(f"raw_{name}").clip(upper_bound=cap_val)
                )

        loaded_metrics[name] = selected
        print(f"    -> {len(selected):,} wallets with {name}")

    if not loaded_metrics:
        print("\nERROR: No metric files found. Run individual metric scripts first.")
        return

    # Build the combined dataframe by joining all metrics on wallet.
    # Use how="full" (Polars >=1.0 renamed "outer" to "full" for full outer joins)
    # and coalesce the wallet column from both sides after each join.
    print(f"\n  Joining {len(loaded_metrics)} metrics...", flush=True)
    metric_names = list(loaded_metrics.keys())
    combined = loaded_metrics[metric_names[0]]

    for i, name in enumerate(metric_names[1:], 2):
        combined = combined.join(
            loaded_metrics[name],
            on="wallet",
            how="full",
            coalesce=True,
        )
        print(f"    Joined {i}/{len(metric_names)}: {name} ({len(combined):,} wallets)", flush=True)

    print(f"  Combined: {len(combined):,} unique wallets", flush=True)

    # Compute percentile rank for each metric (inline, not using dead function).
    # Skip metrics where the non-null population is below MIN_METRIC_POPULATION
    # to avoid inflated percentile scores from small populations.
    print("  Computing percentile ranks...", flush=True)
    score_cols = []
    skipped_metrics = []
    for name in metric_names:
        raw_col = f"raw_{name}"
        score_col = f"score_{name}"
        config = METRIC_FILES[name]

        # Check population size: count of non-null values for this metric
        population_size = combined.select(pl.col(raw_col).drop_nulls().count()).item()
        if population_size < MIN_METRIC_POPULATION:
            skipped_metrics.append((name, population_size))
            # Set score to null for this metric (excluded from aggregation)
            combined = combined.with_columns(
                pl.lit(None).cast(pl.Float64).alias(score_col)
            )
            score_cols.append(score_col)
            continue

        if config["higher_is_suspicious"]:
            # Higher raw value = higher percentile = more suspicious
            combined = combined.with_columns(
                pl.col(raw_col)
                .rank(method="average")
                .truediv(pl.col(raw_col).count())
                .alias(score_col)
            )
        else:
            # Lower raw value = more suspicious, so invert the rank
            combined = combined.with_columns(
                (
                    1.0
                    - pl.col(raw_col)
                    .rank(method="average")
                    .truediv(pl.col(raw_col).count())
                ).alias(score_col)
            )

        score_cols.append(score_col)

    if skipped_metrics:
        print(f"\n  Skipped metrics with < {MIN_METRIC_POPULATION} wallets:")
        for name, pop in skipped_metrics:
            print(f"    {name}: {pop} wallets")

    # Count how many metrics each wallet has
    metric_count_expr = pl.lit(0)
    for name in metric_names:
        score_col = f"score_{name}"
        metric_count_expr = metric_count_expr + (
            pl.when(pl.col(score_col).is_not_null())
            .then(1)
            .otherwise(0)
        )
    combined = combined.with_columns(
        metric_count_expr.alias("num_metrics_available"),
    )

    # Compute weighted aggregate score
    # For each wallet, the aggregate is the weighted sum of available scores
    # Wallets missing a metric get 0 weight for that metric (renormalized)
    weighted_sum_expr = pl.lit(0.0)
    weight_sum_expr = pl.lit(0.0)

    for name in metric_names:
        score_col = f"score_{name}"
        w = WEIGHTS[name]
        weighted_sum_expr = weighted_sum_expr + (
            pl.when(pl.col(score_col).is_not_null())
            .then(pl.col(score_col) * w)
            .otherwise(0.0)
        )
        weight_sum_expr = weight_sum_expr + (
            pl.when(pl.col(score_col).is_not_null())
            .then(pl.lit(w))
            .otherwise(0.0)
        )

    # Apply minimum metric coverage: wallets with fewer than MIN_METRIC_COVERAGE
    # metrics get a null aggregate score (filtered out of rankings)
    combined = combined.with_columns(
        pl.when(
            (weight_sum_expr > 0)
            & (pl.col("num_metrics_available") >= MIN_METRIC_COVERAGE)
        )
        .then(weighted_sum_expr / weight_sum_expr)
        .otherwise(pl.lit(None))
        .alias("aggregate_score"),
        weight_sum_expr.alias("total_weight_coverage"),
    )

    # Sort by aggregate score descending
    combined = combined.sort("aggregate_score", descending=True, nulls_last=True)

    # Add overall rank (only for wallets with a score)
    combined = combined.with_columns(
        pl.when(pl.col("aggregate_score").is_not_null())
        .then(
            pl.col("aggregate_score")
            .rank(method="ordinal", descending=True)
        )
        .otherwise(pl.lit(None))
        .alias("suspicion_rank"),
    )

    # Write output
    print(f"\nWriting {len(combined):,} wallet records to {OUTPUT_PATH}")
    n_scored = combined.filter(pl.col("aggregate_score").is_not_null()).height
    n_excluded = combined.filter(pl.col("aggregate_score").is_null()).height
    print(f"  Scored wallets: {n_scored:,}")
    print(f"  Excluded (< {MIN_METRIC_COVERAGE} metrics): {n_excluded:,}")
    combined.write_parquet(OUTPUT_PATH)

    # Summary
    scored = combined.filter(pl.col("aggregate_score").is_not_null())
    top_n = 20
    print(f"\nTop {top_n} most suspicious wallets:")
    print("-" * 100)

    top = scored.head(top_n)
    for row in top.iter_rows(named=True):
        score_parts = []
        for name in metric_names:
            score_col = f"score_{name}"
            if row.get(score_col) is not None:
                score_parts.append(f"{name[:8]}={row[score_col]:.2f}")

        print(
            f"  #{row['suspicion_rank']:>3d} "
            f"{row['wallet'][:12]}... "
            f"score={row['aggregate_score']:.4f} "
            f"({row['num_metrics_available']}/{len(metric_names)} metrics) "
            f"| {' '.join(score_parts)}"
        )

    print(f"\nScore distribution (scored wallets only):")
    for q in [0.5, 0.75, 0.90, 0.95, 0.99]:
        val = scored.select(pl.col("aggregate_score").quantile(q)).item()
        if val is not None:
            print(f"  p{q:.0%}: {val:.4f}")

    print("Done!")


if __name__ == "__main__":
    main()
