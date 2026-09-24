"""Deterministic, read-only calculations exposed to AI Hub.

The language model may request a calculation, but it never performs the
arithmetic.  This module validates a small, explicit input schema and returns
typed values together with formulas and assumptions suitable for provenance
in the UI.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CalculationName = Literal[
    "percentage_change",
    "dollar_change",
    "return",
    "cagr",
    "weighted_average",
    "position_size",
    "position_risk",
    "position_pnl",
    "risk_reward",
    "allocation",
    "volatility",
    "drawdown",
    "max_drawdown",
    "correlation",
    "options_breakeven",
    "options_intrinsic_value",
    "options_extrinsic_value",
    "options_max_gain_loss",
    "options_assignment_exposure",
    "options_vertical_spread",
    "expected_move",
]


class CalculationRequest(BaseModel):
    """Strict request for one supported calculation.

    Optional fields are operation-specific; :func:`calculate` rejects a
    request that does not provide the required inputs for its operation.
    Unknown fields are rejected so a model cannot smuggle executable input or
    silently change the calculation contract.
    """

    model_config = ConfigDict(extra="forbid")

    calculation: CalculationName
    old_value: float | None = None
    new_value: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    years: float | None = Field(default=None, gt=0)
    entry_price: float | None = Field(default=None, gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    account_value: float | None = Field(default=None, gt=0)
    risk_percent: float | None = Field(default=None, gt=0, le=100)
    shares: float | None = Field(default=None, gt=0)
    position_value: float | None = Field(default=None, ge=0)
    portfolio_value: float | None = Field(default=None, gt=0)
    premium: float | None = Field(default=None, ge=0)
    strike: float | None = Field(default=None, gt=0)
    option_type: Literal["call", "put"] | None = None
    # options_vertical_spread only: the short leg. strike/premium above are
    # the long leg -- two-leg fields stay named for their role (long/short),
    # not their price order, since which strike is higher depends on the
    # spread direction.
    short_strike: float | None = Field(default=None, gt=0)
    short_premium: float | None = Field(default=None, ge=0)
    underlying_price: float | None = Field(default=None, gt=0)
    implied_volatility: float | None = Field(default=None, ge=0)
    days_to_expiration: float | None = Field(default=None, gt=0)
    prices: list[float] | None = None
    comparison_prices: list[float] | None = None
    weights: list[float] | None = None
    peak_value: float | None = Field(default=None, gt=0)
    trough_value: float | None = Field(default=None, gt=0)
    contracts: float | None = Field(default=None, gt=0)
    contract_multiplier: float = Field(default=100, gt=0)

    @model_validator(mode="after")
    def _finite_inputs(self) -> CalculationRequest:
        for name, value in self.__dict__.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.prices is not None:
            if len(self.prices) < 2:
                raise ValueError("prices must contain at least two values")
            if any(not math.isfinite(float(price)) or float(price) <= 0 for price in self.prices):
                raise ValueError("prices must contain finite positive values")
        for name in ("comparison_prices", "weights"):
            series = getattr(self, name)
            if series is not None and any(not math.isfinite(float(value)) for value in series):
                raise ValueError(f"{name} must contain finite values")
        if self.comparison_prices is not None and len(self.comparison_prices) < 2:
            raise ValueError("comparison_prices must contain at least two values")
        return self


class CalculationResult(BaseModel):
    """Verified calculation output with provenance-friendly metadata."""

    calculation: CalculationName
    values: dict[str, float | None]
    formulas: list[str]
    assumptions: list[str]
    source: Literal["MarketLens calculator"] = "MarketLens calculator"


def _require(request: CalculationRequest, *names: str) -> list[float]:
    values: list[float] = []
    for name in names:
        value = getattr(request, name)
        if value is None:
            raise ValueError(f"{name} is required for {request.calculation}")
        values.append(float(value))
    return values


def calculate(request: CalculationRequest) -> CalculationResult:
    """Calculate one supported metric without invoking the model or eval()."""

    op = request.calculation
    values: dict[str, float | None]
    formulas: list[str]
    assumptions: list[str] = []

    if op == "percentage_change":
        old, new = _require(request, "old_value", "new_value")
        if old == 0:
            raise ValueError("old_value must not be zero")
        values = {"percentage_change": (new - old) / abs(old) * 100}
        formulas = ["(new_value - old_value) / abs(old_value) * 100"]
    elif op == "dollar_change":
        old, new = _require(request, "old_value", "new_value")
        values = {"dollar_change": new - old}
        formulas = ["new_value - old_value"]
    elif op == "return":
        start, end = _require(request, "start_value", "end_value")
        if start == 0:
            raise ValueError("start_value must not be zero")
        values = {"return": (end - start) / abs(start) * 100}
        formulas = ["(end_value - start_value) / abs(start_value) * 100"]
    elif op == "cagr":
        start, end, years = _require(request, "start_value", "end_value", "years")
        if start <= 0 or end < 0:
            raise ValueError("start_value must be positive and end_value non-negative")
        values = {"cagr": ((end / start) ** (1 / years) - 1) * 100}
        formulas = ["((end_value / start_value) ** (1 / years) - 1) * 100"]
    elif op == "weighted_average":
        prices = request.prices or []
        weights = request.weights or []
        if len(prices) != len(weights) or not weights or sum(weights) == 0:
            raise ValueError("prices and weights must have the same non-zero length")
        values = {"weighted_average": sum(p * w for p, w in zip(prices, weights, strict=True)) / sum(weights)}
        formulas = ["sum(price[i] * weight[i]) / sum(weight)"]
    elif op == "position_size":
        entry, stop, account, risk_pct = _require(
            request, "entry_price", "stop_price", "account_value", "risk_percent"
        )
        per_share = abs(entry - stop)
        if per_share == 0:
            raise ValueError("entry_price and stop_price must differ")
        risk_dollars = account * risk_pct / 100
        shares = risk_dollars / per_share
        values = {
            "per_share_risk": per_share,
            "risk_dollars": risk_dollars,
            "shares": shares,
            "position_value": shares * entry,
            "portfolio_risk_percent": risk_pct,
        }
        formulas = [
            "abs(entry_price - stop_price)",
            "account_value * risk_percent / 100",
            "risk_dollars / per_share_risk",
        ]
        assumptions.append("Position size is fractional; round down to whole shares before placing an order.")
    elif op == "position_risk":
        # Risk of a position the trader already sized: per-share, total, and
        # (when inputs allow) portfolio-risk percent and reward/risk, each a
        # distinct output rather than one blended number.
        entry, stop, shares = _require(request, "entry_price", "stop_price", "shares")
        per_share = abs(entry - stop)
        if per_share == 0:
            raise ValueError("entry_price and stop_price must differ")
        values = {
            "per_share_risk": per_share,
            "total_risk": per_share * shares,
            "position_value": entry * shares,
            "portfolio_risk_percent": None,
            "reward_risk": None,
        }
        formulas = ["abs(entry_price - stop_price)", "per_share_risk * shares", "entry_price * shares"]
        if request.account_value is not None:
            values["portfolio_risk_percent"] = per_share * shares / float(request.account_value) * 100
            formulas.append("total_risk / account_value * 100")
        else:
            assumptions.append("Tell me your account value to get portfolio-risk percent.")
        if request.target_price is not None:
            values["reward_risk"] = abs(float(request.target_price) - entry) / per_share
            formulas.append("abs(target_price - entry_price) / per_share_risk")
        else:
            assumptions.append("Tell me a target price to get reward/risk.")
    elif op == "position_pnl":
        # Realized/unrealized P&L of a position held from entry to exit.
        entry, exit_p, shares = _require(request, "entry_price", "exit_price", "shares")
        per_share = exit_p - entry
        values = {
            "per_share_pnl": per_share,
            "total_pnl": per_share * shares,
            "cost_basis": entry * shares,
            "exit_value": exit_p * shares,
            "return_percent": per_share / entry * 100,
        }
        formulas = [
            "exit_price - entry_price",
            "per_share_pnl * shares",
            "entry_price * shares",
            "exit_price * shares",
            "per_share_pnl / entry_price * 100",
        ]
        assumptions.append("Excludes commissions, fees, dividends, and taxes.")
    elif op == "risk_reward":
        entry, stop, target = _require(request, "entry_price", "stop_price", "target_price")
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk == 0:
            raise ValueError("entry_price and stop_price must differ")
        values = {"risk": risk, "reward": reward, "risk_reward": reward / risk}
        formulas = ["abs(entry_price - stop_price)", "abs(target_price - entry_price)", "reward / risk"]
    elif op == "allocation":
        position, portfolio = _require(request, "position_value", "portfolio_value")
        values = {"allocation_percent": position / portfolio * 100}
        formulas = ["position_value / portfolio_value * 100"]
    elif op == "volatility":
        prices = [float(price) for price in (request.prices or [])]
        returns = [(prices[i] / prices[i - 1]) - 1 for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        stddev = math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))
        values = {"period_volatility": stddev * 100}
        formulas = ["sample_stddev(price[i] / price[i-1] - 1) * 100"]
        assumptions.append("Volatility is the sample standard deviation of simple sequential returns; no annualization is applied.")
    elif op == "drawdown":
        peak, trough = _require(request, "peak_value", "trough_value")
        values = {"drawdown_percent": (peak - trough) / peak * 100}
        formulas = ["(peak_value - trough_value) / peak_value * 100"]
    elif op == "max_drawdown":
        prices = [float(price) for price in (request.prices or [])]
        peak = prices[0]
        maximum = 0.0
        for price in prices:
            peak = max(peak, price)
            maximum = max(maximum, (peak - price) / peak * 100)
        values = {"maximum_drawdown_percent": maximum}
        formulas = ["max((running_peak - price) / running_peak * 100)"]
    elif op == "correlation":
        left = [float(price) for price in (request.prices or [])]
        right = [float(price) for price in (request.comparison_prices or [])]
        if len(left) != len(right) or len(left) < 2:
            raise ValueError("prices and comparison_prices must have the same length (at least two)")
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
        left_dev = math.sqrt(sum((a - left_mean) ** 2 for a in left))
        right_dev = math.sqrt(sum((b - right_mean) ** 2 for b in right))
        if left_dev == 0 or right_dev == 0:
            raise ValueError("correlation requires variation in both series")
        values = {"correlation": covariance / (left_dev * right_dev)}
        formulas = ["covariance(prices, comparison_prices) / (stddev(prices) * stddev(comparison_prices))"]
    elif op == "options_breakeven":
        strike, premium = _require(request, "strike", "premium")
        option_type = request.option_type
        if option_type is None:
            raise ValueError("option_type is required for options_breakeven")
        values = {"breakeven": strike + premium if option_type == "call" else strike - premium}
        formulas = ["strike + premium (call)" if option_type == "call" else "strike - premium (put)"]
        assumptions.append("Breakeven excludes commissions, fees, taxes, and early assignment effects.")
    elif op in ("options_intrinsic_value", "options_extrinsic_value", "options_max_gain_loss"):
        underlying, strike, premium = _require(request, "underlying_price", "strike", "premium")
        option_type = request.option_type
        if option_type is None:
            raise ValueError(f"option_type is required for {op}")
        intrinsic = max(underlying - strike, 0) if option_type == "call" else max(strike - underlying, 0)
        if op == "options_intrinsic_value":
            values = {"intrinsic_value": intrinsic}
            formulas = ["max(underlying_price - strike, 0) (call)" if option_type == "call" else "max(strike - underlying_price, 0) (put)"]
        elif op == "options_extrinsic_value":
            values = {"extrinsic_value": max(premium - intrinsic, 0)}
            formulas = ["max(premium - intrinsic_value, 0)"]
        else:
            max_gain = None if option_type == "call" else max(strike - premium, 0)
            values = {"max_gain_per_share": max_gain, "max_loss_per_share": premium}
            formulas = ["unlimited (call) or strike - premium (put)", "premium"]
            assumptions.append("Maximum gain for a long call is theoretically unlimited; values are per share.")
    elif op == "options_assignment_exposure":
        strike, contracts = _require(request, "strike", "contracts")
        option_type = request.option_type
        if option_type is None:
            raise ValueError("option_type is required for options_assignment_exposure")
        multiplier = request.contract_multiplier
        shares = contracts * multiplier
        cash_exposure = strike * shares
        values = {
            "assignment_shares": shares,
            "assignment_cash_exposure": cash_exposure,
        }
        formulas = [
            "contracts * contract_multiplier",
            "strike * assignment_shares",
        ]
        assumptions.append(
            "A short put assigned delivers this cash exposure to buy shares; a short call assigned delivers this "
            "many shares for sale at strike. Uses a 100-share standard equity option multiplier unless overridden."
        )
    elif op == "options_vertical_spread":
        long_strike, long_premium, short_strike, short_premium = _require(
            request, "strike", "premium", "short_strike", "short_premium"
        )
        option_type = request.option_type
        if option_type is None:
            raise ValueError("option_type is required for options_vertical_spread")
        if long_strike == short_strike:
            raise ValueError("strike and short_strike must differ for a vertical spread")
        width = abs(long_strike - short_strike)
        net_debit = long_premium - short_premium
        # Derived from each structure's expiration payoff (max(S-K,0) minus
        # the short leg's, or the put mirror). Calls: long the lower strike
        # is the debit structure (bull call spread); puts: long the HIGHER
        # strike is the debit structure (bear put spread) -- calls and puts
        # are not symmetric in strike order, so each gets its own branch.
        if option_type == "call":
            if long_strike < short_strike:
                max_gain, max_loss, breakeven = width - net_debit, net_debit, long_strike + net_debit
            else:
                max_gain, max_loss, breakeven = -net_debit, width + net_debit, short_strike - net_debit
        else:
            if long_strike > short_strike:
                max_gain, max_loss, breakeven = width - net_debit, net_debit, long_strike - net_debit
            else:
                max_gain, max_loss, breakeven = -net_debit, width + net_debit, short_strike + net_debit
        multiplier = request.contract_multiplier
        contracts = request.contracts or 1
        values = {
            "net_debit": net_debit,
            "max_gain_per_share": max_gain,
            "max_loss_per_share": max_loss,
            "breakeven": breakeven,
            "max_gain_total": max_gain * multiplier * contracts,
            "max_loss_total": max_loss * multiplier * contracts,
        }
        formulas = [
            "net_debit = long_premium - short_premium",
            "width = abs(long_strike - short_strike)",
            "call, long_strike < short_strike: max_gain = width - net_debit, max_loss = net_debit, breakeven = long_strike + net_debit",
            "call, long_strike > short_strike: max_gain = -net_debit, max_loss = width + net_debit, breakeven = short_strike - net_debit",
            "put, long_strike > short_strike: max_gain = width - net_debit, max_loss = net_debit, breakeven = long_strike - net_debit",
            "put, long_strike < short_strike: max_gain = -net_debit, max_loss = width + net_debit, breakeven = short_strike + net_debit",
        ]
        assumptions.append(
            "Assumes both legs share the same expiration and underlying and are opened/closed together; "
            "excludes commissions, fees, and early assignment risk on the short leg."
        )
    else:  # expected_move
        price, iv, days = _require(request, "new_value", "implied_volatility", "days_to_expiration")
        if iv > 10:
            iv /= 100
        move = price * iv * math.sqrt(days / 365)
        values = {"expected_move": move, "lower_bound": price - move, "upper_bound": price + move}
        formulas = ["price * implied_volatility * sqrt(days_to_expiration / 365)"]
        assumptions.append("Implied volatility is treated as a decimal; values above 10 are interpreted as percentages.")

    return CalculationResult(
        calculation=op,
        values={key: (round(value, 10) if value is not None else None) for key, value in values.items()},
        formulas=formulas,
        assumptions=assumptions,
    )
