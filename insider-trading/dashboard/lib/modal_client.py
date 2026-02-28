"""Thin client for calling Modal explore functions from the dashboard."""

import modal
import polars as pl

_APP_NAME = "polymarket-explore"


def _lookup(fn_name: str):
    return modal.Function.from_name(_APP_NAME, fn_name)


def _deser(raw: bytes) -> pl.DataFrame:
    return pl.DataFrame.deserialize(raw, format="binary")


def fetch_markets() -> pl.DataFrame:
    """Fetch markets list from Modal volume."""
    raw = _lookup("list_markets").remote()
    return _deser(raw)


def remote_analyze_market(market_id: int) -> dict[str, pl.DataFrame]:
    """Run full signal1 + signal2 analysis for one market on Modal."""
    raw = _lookup("analyze_market").remote(market_id)
    return {k: _deser(v) for k, v in raw.items()}


def remote_analyze_wallet(wallet: str) -> dict[str, pl.DataFrame]:
    """Load positions + run signal1 metrics for one wallet on Modal."""
    raw = _lookup("analyze_wallet").remote(wallet)
    return {k: _deser(v) for k, v in raw.items()}
