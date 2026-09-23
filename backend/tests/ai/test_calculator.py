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
