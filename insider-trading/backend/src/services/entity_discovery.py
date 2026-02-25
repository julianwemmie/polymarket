"""Entity market discovery utilities."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Any

from src.services.polymarket import polymarket_client


def _extract_condition_id(raw: dict[str, Any]) -> str:
    return str(
        raw.get("conditionId")
        or raw.get("condition_id")
        or raw.get("id")
        or ""
    )


def _extract_winning_outcome(raw: dict[str, Any]) -> str | None:
    outcome = raw.get("outcome") or raw.get("resolution")
    if outcome:
        normalized = str(outcome).strip()
        return normalized if normalized else None

    prices = raw.get("outcomePrices")
    try:
        if isinstance(prices, str):
            import json as _json

            prices = _json.loads(prices)
        if isinstance(prices, list) and prices:
            yes_price = float(prices[0])
            if yes_price > 0.9:
                return "Yes"
            if yes_price < 0.1:
                return "No"
    except (ValueError, TypeError, IndexError):
        pass

    return None


def _is_resolved(raw: dict[str, Any], winning_outcome: str | None) -> bool:
    if winning_outcome:
        closed = raw.get("closed")
        active = raw.get("active")
        if closed is True:
            return True
        if active is False:
            return True
    return False


def _normalize_market(raw: dict[str, Any], match_term: str) -> dict[str, Any] | None:
    condition_id = _extract_condition_id(raw)
    if not condition_id:
        return None

    question = str(raw.get("question") or raw.get("title") or "").strip()
    if not question:
        return None

    winning_outcome = _extract_winning_outcome(raw)
    resolved = _is_resolved(raw, winning_outcome)

    return {
        "condition_id": condition_id,
        "question": question,
        "slug": raw.get("slug"),
        "volume": float(raw.get("volumeNum", raw.get("volume", 0)) or 0),
        "resolved": resolved,
        "winning_outcome": winning_outcome,
        "_match_term": match_term,
        "_match_score": 0,
        "match_terms": [match_term],
        "included": True,
    }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_normalize_text(v) for v in value)
    return str(value).lower()


def _parse_outcomes(raw_market: dict[str, Any]) -> list[str]:
    outcomes = raw_market.get("outcomes")
    if outcomes is None:
        return []

    parsed: list[str] = []
    try:
        if isinstance(outcomes, str):
            import json as _json

            decoded = _json.loads(outcomes)
            if isinstance(decoded, list):
                parsed = [str(v) for v in decoded]
            else:
                parsed = [str(outcomes)]
        elif isinstance(outcomes, list):
            parsed = [str(v) for v in outcomes]
        else:
            parsed = [str(outcomes)]
    except Exception:
        parsed = [str(outcomes)]

    return [p for p in parsed if p]


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text) is not None


def _term_tokens(term: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-z0-9]+", term.lower()) if tok]


def _market_match_score(
    term: str,
    raw_market: dict[str, Any],
    event_title: str = "",
) -> int:
    """Return a relevance score for a market-term match (0 means no match).

    We prioritize question/slug matches and only use metadata fields (tags,
    descriptions, event title) as secondary context to avoid unrelated results.
    """
    query = term.strip().lower()
    if not query:
        return 0

    question = _normalize_text(raw_market.get("question") or raw_market.get("title"))
    slug = _normalize_text(raw_market.get("slug"))
    primary = f"{question} {slug}".strip()
    if not primary:
        return 0

    option_fields = " ".join(
        [
            _normalize_text(raw_market.get("groupItemTitle")),
            _normalize_text(_parse_outcomes(raw_market)),
        ]
    ).strip()

    secondary = " ".join(
        [
            _normalize_text(raw_market.get("tags")),
            _normalize_text(raw_market.get("description")),
            _normalize_text(event_title),
        ]
    ).strip()

    tokens = _term_tokens(query)
    core_tokens = [t for t in tokens if len(t) >= 3] or tokens
    if not core_tokens:
        return 0

    # Strong exact phrase in question/slug.
    if query in primary:
        return 4
    if query in option_fields:
        return 4

    primary_hits = sum(1 for token in core_tokens if _contains_token(primary, token))
    option_hits = sum(1 for token in core_tokens if _contains_token(option_fields, token))
    secondary_hits = sum(1 for token in core_tokens if _contains_token(secondary, token))

    # Single-word terms must appear as a token in question/slug.
    if len(core_tokens) == 1:
        if primary_hits == 1 or option_hits == 1:
            return 3
        return 0

    # Multi-term phrase fully found in strong fields.
    if primary_hits == len(core_tokens) or option_hits == len(core_tokens):
        return 3

    # Multi-term partial in primary/options, remainder in secondary context.
    strong_hits = max(primary_hits, option_hits)
    if strong_hits >= 1 and (strong_hits + secondary_hits) == len(core_tokens):
        return 2

    return 0


async def discover_entity_markets(
    search_terms: list[str],
    per_term_limit: int = 120,
) -> list[dict[str, Any]]:
    """Discover markets related to an entity from multiple search terms.

    For each term we query both Gamma events and markets, then deduplicate by
    ``condition_id`` and annotate markets with ``_match_term``/``match_terms``.
    """
    cleaned_terms = [t.strip() for t in search_terms if t and t.strip()]
    if not cleaned_terms:
        return []

    dedup: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for term in cleaned_terms:
        event_results, open_market_results, resolved_market_results = await asyncio.gather(
            polymarket_client.search_events(term, limit=per_term_limit, closed=False),
            polymarket_client.search_markets(term, limit=per_term_limit, closed=False),
            polymarket_client.search_markets(term, limit=per_term_limit, closed=True),
        )

        for event in event_results:
            for raw_market in event.get("markets", []):
                score = _market_match_score(
                    term,
                    raw_market,
                    event_title=str(event.get("title") or ""),
                )
                if score <= 0:
                    continue
                normalized = _normalize_market(raw_market, term)
                if not normalized:
                    continue
                normalized["_match_score"] = score
                cid = normalized["condition_id"]
                existing = dedup.get(cid)
                if existing is None:
                    dedup[cid] = normalized
                else:
                    existing["_match_score"] = max(
                        int(existing.get("_match_score", 0)),
                        score,
                    )
                    if term not in existing["match_terms"]:
                        existing["match_terms"].append(term)

        for raw_market in [*open_market_results, *resolved_market_results]:
            score = _market_match_score(term, raw_market)
            if score <= 0:
                continue
            normalized = _normalize_market(raw_market, term)
            if not normalized:
                continue
            normalized["_match_score"] = score
            cid = normalized["condition_id"]
            existing = dedup.get(cid)
            if existing is None:
                dedup[cid] = normalized
            else:
                existing["_match_score"] = max(
                    int(existing.get("_match_score", 0)),
                    score,
                )
                if term not in existing["match_terms"]:
                    existing["match_terms"].append(term)

    discovered = list(dedup.values())
    discovered.sort(
        key=lambda m: (
            len(m.get("match_terms", [])),
            int(m.get("_match_score", 0)),
            bool(m.get("resolved", False)),
            float(m.get("volume", 0) or 0),
        ),
        reverse=True,
    )
    return discovered
