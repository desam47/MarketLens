import pytest

from backend.ai.verified_formula import evaluate_formula


def test_formula_uses_only_verified_values() -> None:
    assert evaluate_formula("risk * 2 + reward", {"risk": 5, "reward": 10}) == 20


@pytest.mark.parametrize("expression", ["__import__('os')", "risk.__class__", "values[0]"])
def test_formula_rejects_code_access(expression: str) -> None:
    with pytest.raises(ValueError, match="unsupported|unknown"):
        evaluate_formula(expression, {"risk": 5})


def test_formula_rejects_unknown_values_and_zero_division() -> None:
    with pytest.raises(ValueError, match="unknown verified value"):
        evaluate_formula("risk + missing", {"risk": 5})
    with pytest.raises(ValueError, match="divides by zero"):
        evaluate_formula("risk / zero", {"risk": 5, "zero": 0})
