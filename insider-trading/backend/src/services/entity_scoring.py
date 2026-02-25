"""Entity-based wallet scoring using win-rate comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EntityScoreResult:
    entity_win_rate: float | None
    overall_win_rate: float | None
    win_rate_delta: float | None
    suspicion_score: float | None
    is_flagged: bool
    reasons: list[str]


class EntityScoringEngine:
    """Simple scoring engine: entity win rate + overall baseline context."""

    def score_wallet(
        self,
        entity_wins: int,
        entity_losses: int,
        overall_wins: int,
        overall_losses: int,
    ) -> EntityScoreResult:
        entity_resolved = entity_wins + entity_losses
        overall_resolved = overall_wins + overall_losses

        entity_win_rate = (
            entity_wins / entity_resolved if entity_resolved > 0 else None
        )
        overall_win_rate = (
            overall_wins / overall_resolved if overall_resolved > 0 else None
        )

        delta = None
        if entity_win_rate is not None and overall_win_rate is not None:
            delta = entity_win_rate - overall_win_rate

        reasons: list[str] = []
        if entity_win_rate is None:
            reasons.append("Insufficient resolved entity markets to score")
            return EntityScoreResult(
                entity_win_rate=None,
                overall_win_rate=overall_win_rate,
                win_rate_delta=delta,
                suspicion_score=None,
                is_flagged=False,
                reasons=reasons,
            )

        suspicion_score = round(entity_win_rate, 4)
        if overall_win_rate is not None and delta is not None:
            reasons.append(
                f"Entity win rate {entity_win_rate:.1%} vs overall {overall_win_rate:.1%} (delta {delta:+.1%})"
            )
        else:
            reasons.append(f"Entity win rate {entity_win_rate:.1%}")

        if entity_resolved >= 3 and entity_win_rate >= 0.8:
            reasons.append("High entity win rate across >=3 resolved entity markets")
            flagged = True
        else:
            flagged = False

        return EntityScoreResult(
            entity_win_rate=entity_win_rate,
            overall_win_rate=overall_win_rate,
            win_rate_delta=delta,
            suspicion_score=suspicion_score,
            is_flagged=flagged,
            reasons=reasons,
        )


entity_scoring_engine = EntityScoringEngine()
