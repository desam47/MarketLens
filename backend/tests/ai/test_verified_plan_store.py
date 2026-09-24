"""Tests for short-lived verified-plan tracking handles."""

from unittest.mock import patch

from backend.ai import verified_plan_store as store
from backend.ai.prompt import TradePlan


def _plan() -> TradePlan:
    return TradePlan(
        recommendation="buy",
        conviction="medium",
        time_horizon="swing",
        entry_zone_low=100.0,
        entry_zone_high=102.0,
        stop_loss=96.0,
        targets=[108.0],
        risk_reward=1.5,
        thesis="Test setup.",
        invalidation="Below 96.",
    )


def setup_function():
    store._clear_verified_plan_store()


def teardown_function():
    store._clear_verified_plan_store()


def test_only_verified_actionable_plans_receive_a_handle():
    assert store.issue_verified_plan(
        symbol="AAPL",
        timeframe="1d",
        provider="ollama",
        model="llama3.2",
        plan=_plan(),
        validation={"status": "unavailable"},
    ) is None

    plan_id = store.issue_verified_plan(
        symbol="aapl",
        timeframe="1d",
        provider="ollama",
        model="llama3.2",
        plan=_plan(),
        validation={"status": "verified"},
    )

    assert plan_id is not None
    stored = store.get_verified_plan(plan_id)
    assert stored is not None
    assert stored.symbol == "AAPL"
    assert stored.plan == _plan()


def test_background_source_key_reuses_the_same_unexpired_handle():
    kwargs = dict(
        symbol="AAPL",
        timeframe="1d",
        provider="ollama",
        model="llama3.2",
        plan=_plan(),
        validation={"status": "verified"},
        source_key="background-job:abc",
    )
    assert store.issue_verified_plan(**kwargs) == store.issue_verified_plan(**kwargs)


def test_expired_handle_cannot_be_retrieved():
    with patch("backend.ai.verified_plan_store.time.monotonic", side_effect=(0.0, 601.0)):
        plan_id = store.issue_verified_plan(
            symbol="AAPL",
            timeframe="1d",
            provider="ollama",
            model="llama3.2",
            plan=_plan(),
            validation={"status": "verified"},
        )
        assert plan_id is not None
        assert store.get_verified_plan(plan_id) is None
