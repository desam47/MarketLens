"""Restricted arithmetic over verified tool-result values.

Only numeric constants, named values, parentheses, and arithmetic operators
are accepted. Calls, attributes, indexing, comprehensions, and all other AST
nodes are rejected before evaluation.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate_formula(expression: str, values: Mapping[str, float]) -> float:
    if len(expression) > 200:
        raise ValueError("formula is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("formula is not valid arithmetic") from exc

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"unknown verified value: {node.id}")
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("formula divides by zero")
            result = _BINARY[type(node.op)](left, right)
            if not math.isfinite(result):
                raise ValueError("formula result is not finite")
            return float(result)
        raise ValueError("formula contains an unsupported expression")

    return visit(tree)
