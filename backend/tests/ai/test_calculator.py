import pytest
from pydantic import ValidationError

from backend.ai.calculator import CalculationRequest, calculate


def test_position_size_returns_verified_risk_breakdown() -> None:
    result = calculate(
        CalculationRequest(
            calculation="position_size",
            entry_price=220,
            stop_price=212,
            account_value=100_000,
            risk_percent=1,
        )
    )

    assert result.values["per_share_risk"] == 8
    assert result.values["risk_dollars"] == 1000
    assert result.values["shares"] == 125
    assert result.values["position_value"] == 27_500
    assert result.source == "MarketLens calculator"
    assert result.formulas


def test_percentage_change_uses_absolute_baseline() -> None:
    result = calculate(
        CalculationRequest(calculation="percentage_change", old_value=-10, new_value=-5)
    )

    assert result.values["percentage_change"] == 50


def test_expected_move_accepts_iv_percentage() -> None:
    result = calculate(
        CalculationRequest(
            calculation="expected_move",
            new_value=100,
            implied_volatility=36.5,
            days_to_expiration=30,
        )
    )

    assert result.values["expected_move"] == pytest.approx(10.4642, rel=1e-4)
    assert result.values["lower_bound"] < 100 < result.values["upper_bound"]


def test_weighted_average_correlation_and_max_drawdown() -> None:
    weighted = calculate(
        CalculationRequest(calculation="weighted_average", prices=[10, 20], weights=[1, 3])
    )
    correlation = calculate(
        CalculationRequest(
            calculation="correlation",
            prices=[1, 2, 3],
            comparison_prices=[2, 4, 6],
        )
    )
    drawdown = calculate(CalculationRequest(calculation="max_drawdown", prices=[100, 80, 90, 70]))

    assert weighted.values["weighted_average"] == 17.5
    assert correlation.values["correlation"] == 1
    assert drawdown.values["maximum_drawdown_percent"] == 30


def test_option_values_are_explicit_and_json_safe() -> None:
    result = calculate(
        CalculationRequest(
            calculation="options_max_gain_loss",
            option_type="call",
            underlying_price=105,
            strike=100,
            premium=4,
        )
    )

    assert result.values == {"max_gain_per_share": None, "max_loss_per_share": 4}


def test_options_assignment_exposure_uses_standard_contract_multiplier() -> None:
    result = calculate(
        CalculationRequest(
            calculation="options_assignment_exposure",
            option_type="put",
            strike=50,
            contracts=2,
        )
    )

    assert result.values["assignment_shares"] == 200
    assert result.values["assignment_cash_exposure"] == 10_000
    assert result.assumptions


def test_options_vertical_spread_bull_call_debit() -> None:
    # Long 100c @ 5, short 110c @ 2.
    result = calculate(CalculationRequest(
        calculation="options_vertical_spread", option_type="call",
        strike=100, premium=5, short_strike=110, short_premium=2,
    ))
    assert result.values["net_debit"] == 3
    assert result.values["max_gain_per_share"] == 7
    assert result.values["max_loss_per_share"] == 3
    assert result.values["breakeven"] == 103


def test_options_vertical_spread_bear_call_credit() -> None:
    # Long 110c @ 2, short 100c @ 5.
    result = calculate(CalculationRequest(
        calculation="options_vertical_spread", option_type="call",
        strike=110, premium=2, short_strike=100, short_premium=5,
    ))
    assert result.values["net_debit"] == -3
    assert result.values["max_gain_per_share"] == 3
    assert result.values["max_loss_per_share"] == 7
    assert result.values["breakeven"] == 103


def test_options_vertical_spread_bear_put_debit() -> None:
    # Long 110p @ 6, short 100p @ 2.
    result = calculate(CalculationRequest(
        calculation="options_vertical_spread", option_type="put",
        strike=110, premium=6, short_strike=100, short_premium=2,
    ))
    assert result.values["net_debit"] == 4
    assert result.values["max_gain_per_share"] == 6
    assert result.values["max_loss_per_share"] == 4
    assert result.values["breakeven"] == 106


def test_options_vertical_spread_bull_put_credit() -> None:
    # Long 100p @ 2, short 110p @ 6.
    result = calculate(CalculationRequest(
        calculation="options_vertical_spread", option_type="put",
        strike=100, premium=2, short_strike=110, short_premium=6,
    ))
    assert result.values["net_debit"] == -4
    assert result.values["max_gain_per_share"] == 4
    assert result.values["max_loss_per_share"] == 6
    assert result.values["breakeven"] == 106
    # Scaled totals use contract_multiplier (default 100) * contracts (default 1).
    assert result.values["max_gain_total"] == 400
    assert result.values["max_loss_total"] == 600


def test_options_vertical_spread_rejects_identical_strikes() -> None:
    with pytest.raises(ValueError, match="differ"):
        calculate(CalculationRequest(
            calculation="options_vertical_spread", option_type="call",
            strike=100, premium=5, short_strike=100, short_premium=2,
        ))


def test_unknown_inputs_and_missing_operation_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CalculationRequest(calculation="dollar_change", old_value=1, new_value=2, code="1+1")

    with pytest.raises(ValueError, match="old_value"):
        calculate(CalculationRequest(calculation="percentage_change", new_value=2))


def test_calculator_does_not_evaluate_formulas() -> None:
    with pytest.raises(ValueError, match="old_value"):
        calculate(CalculationRequest(calculation="percentage_change", new_value=2))


def test_position_risk_separates_per_share_total_and_portfolio_risk() -> None:
    result = calculate(CalculationRequest(
        calculation="position_risk", entry_price=220, stop_price=212, shares=200,
        target_price=236, account_value=100_000,
    ))
    assert result.values == {
        "per_share_risk": 8,
        "total_risk": 1600,
        "position_value": 44_000,
        "portfolio_risk_percent": 1.6,
        "reward_risk": 2,
    }


def test_position_risk_leaves_unavailable_outputs_empty() -> None:
    result = calculate(CalculationRequest(calculation="position_risk", entry_price=220, stop_price=212, shares=200))
    assert result.values["total_risk"] == 1600
    assert result.values["portfolio_risk_percent"] is None
    assert result.values["reward_risk"] is None
    assert any("account_value" in assumption for assumption in result.assumptions)


def test_position_risk_requires_shares() -> None:
    with pytest.raises(ValueError, match="shares"):
        calculate(CalculationRequest(calculation="position_risk", entry_price=220, stop_price=212))


# Golden values below are computed by hand, independently of calculator.py.
@pytest.mark.parametrize(
    ("request_fields", "expected"),
    [
        ({"calculation": "return", "start_value": 200, "end_value": 250}, {"return": 25.0}),
        ({"calculation": "cagr", "start_value": 100, "end_value": 121, "years": 2}, {"cagr": 10.0}),
        # returns +10% then -10%: mean 0, sample variance (0.01 + 0.01) / 1 = 0.02
        ({"calculation": "volatility", "prices": [100, 110, 99]}, {"period_volatility": 14.142135623730951}),
        ({"calculation": "drawdown", "peak_value": 200, "trough_value": 150}, {"drawdown_percent": 25.0}),
        ({"calculation": "risk_reward", "entry_price": 100, "stop_price": 95, "target_price": 110}, {"risk": 5.0, "reward": 10.0, "risk_reward": 2.0}),
        ({"calculation": "allocation", "position_value": 25_000, "portfolio_value": 100_000}, {"allocation_percent": 25.0}),
        ({"calculation": "options_breakeven", "option_type": "call", "strike": 100, "premium": 4}, {"breakeven": 104.0}),
        ({"calculation": "options_breakeven", "option_type": "put", "strike": 100, "premium": 4}, {"breakeven": 96.0}),
        ({"calculation": "options_intrinsic_value", "option_type": "call", "underlying_price": 105, "strike": 100, "premium": 7}, {"intrinsic_value": 5.0}),
        ({"calculation": "options_intrinsic_value", "option_type": "put", "underlying_price": 105, "strike": 100, "premium": 2}, {"intrinsic_value": 0.0}),
        ({"calculation": "options_extrinsic_value", "option_type": "call", "underlying_price": 105, "strike": 100, "premium": 7}, {"extrinsic_value": 2.0}),
    ],
)
def test_golden_formula_values(request_fields: dict, expected: dict) -> None:
    result = calculate(CalculationRequest(**request_fields))
    assert result.values == pytest.approx(expected)
    assert result.formulas


@pytest.mark.parametrize(
    "request_fields",
    [
        {"calculation": "return", "start_value": 0, "end_value": 10},
        {"calculation": "cagr", "start_value": -5, "end_value": 10, "years": 1},
        {"calculation": "risk_reward", "entry_price": 100, "stop_price": 100, "target_price": 110},
        {"calculation": "options_breakeven", "strike": 100, "premium": 4},
    ],
)
def test_invalid_inputs_are_rejected_not_defaulted(request_fields: dict) -> None:
    with pytest.raises(ValueError):
        calculate(CalculationRequest(**request_fields))
