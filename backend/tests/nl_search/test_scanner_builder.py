"""Tests for Phase 5.5.1's non-executing Scanner Builder preview."""

from backend.nl_search.scanner_builder import build_scanner_preview
from backend.nl_search.schema import NLFilters
from backend.scanner.filters import default_registry


def _as_pairs(preview):
    return {(item.type, tuple(sorted(item.params.items()))) for item in preview.filters}


def test_preview_maps_the_documented_oversold_volume_query_to_editable_filters() -> None:
    preview = build_scanner_preview(
        "Find oversold stocks with rising volume above their 20-day average"
    )

    pairs = _as_pairs(preview)
    assert ("rsi_oversold", (("threshold", 30.0),)) in pairs
    assert ("volume_expansion", (("min_ratio", 1.5),)) in pairs
    assert preview.match == "AND"
    assert preview.ambiguous is False


def test_preview_supports_session_catalyst_and_microstructure_filters_without_ai() -> None:
    preview = build_scanner_preview(
        "Premarket breakouts with tight spread below 8 bps, buy tape pressure, "
        "large prints, and avoid earnings within 7 days"
    )

    pairs = _as_pairs(preview)
    assert ("market_session", (("session", "premarket"),)) in pairs
    assert ("breakout", (("lookback", "20"), ("min_breakout_pct", 0))) in pairs
    assert ("tight_spread", (("max_spread_bps", 8.0),)) in pairs
    assert ("tape_pressure", (("direction", "buy"),)) in pairs
    assert ("large_print_activity", (("min_blocks", 1),)) in pairs
    assert ("exclude_earnings_within_days", (("days", "7"),)) in pairs
    assert preview.parser_used == "rules"


def test_every_preview_filter_is_accepted_by_the_execution_registry() -> None:
    preview = build_scanner_preview(
        "Above the 50 day SMA with volatility squeeze, trade-rate spike, bid heavy imbalance, "
        "and live volume acceleration"
    )

    for filter_spec in preview.filters:
        if filter_spec.type != "exclude_earnings_within_days":
            default_registry.build(filter_spec.model_dump())
    assert preview.filters


def test_preview_uses_the_scanner_parameter_names_for_price_and_mtf_alignment() -> None:
    preview = build_scanner_preview("price above $100 with all timeframes aligned")

    pairs = _as_pairs(preview)
    assert ("price_above", (("price", 100.0),)) in pairs
    assert ("mtf_alignment", (("min_confidence", 0.5), ("min_timeframes", 3))) in pairs
    for filter_spec in preview.filters:
        default_registry.build(filter_spec.model_dump())


def test_unrecognized_criteria_require_review_instead_of_a_hidden_match_all_scan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.nl_search.scanner_builder.parse_query",
        lambda query, base: (NLFilters(), None, "default"),
    )

    preview = build_scanner_preview("find cosmic alpha resonance")

    assert preview.filters == []
    assert preview.ambiguous is True
    assert preview.unresolved
