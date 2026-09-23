"""CRUD and built-in definitions for Chat workflows."""

import json

from backend.models import ChatWorkflow
from backend.utils.timezone import now_ny

BUILTIN_WORKFLOWS = (
    {
        "name": "Morning Review",
        "description": "Market regime, watchlist, and catalyst context.",
        "steps": [
            {"tool": "get_market_regime", "arguments": {}, "requires_confirmation": False},
            {"tool": "get_watchlist", "arguments": {}, "requires_confirmation": False},
            {"tool": "get_calendar", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
        ],
        "parameters": {"symbol": "AAPL"},
        "output_layout": "summary",
    },
    {
        "name": "Evaluate a Breakout",
        "description": "Review trend, confluence, and tape for one symbol.",
        "steps": [
            {"tool": "get_trend", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
            {"tool": "get_confluence", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
            {"tool": "get_tape_state", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
        ],
        "parameters": {"symbol": "AAPL"},
        "output_layout": "evidence_table",
    },
    {
        "name": "Options Setup Review",
        "description": "Review an options chain with delayed-data labels.",
        "steps": [
            {"tool": "get_options_snapshot", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
            {"tool": "get_quote", "arguments": {"symbol": "{{symbol}}"}, "requires_confirmation": False},
        ],
        "parameters": {"symbol": "AAPL"},
        "output_layout": "options_card",
    },
    {
        "name": "End-of-Day Journal Review",
        "description": "Review journal entries and risk context without placing orders.",
        "steps": [
            {"tool": "get_trade_journal", "arguments": {}, "requires_confirmation": False},
            {"tool": "get_risk_dashboard", "arguments": {}, "requires_confirmation": False},
        ],
        "parameters": {},
        "output_layout": "review",
    },
)


class WorkflowRepository:
    def __init__(self, db):
        self.db = db

    def ensure_builtins(self) -> None:
        existing = {row.name for row in self.db.query(ChatWorkflow).all()}
        for item in BUILTIN_WORKFLOWS:
            if item["name"] in existing:
                continue
            self.db.add(
                ChatWorkflow(
                    name=item["name"],
                    description=item["description"],
                    steps=json.dumps(item["steps"], sort_keys=True),
                    parameters=json.dumps(item["parameters"], sort_keys=True),
                    output_layout=item["output_layout"],
                    is_builtin=True,
                )
            )
        self.db.commit()

    def list(self) -> list[ChatWorkflow]:
        self.ensure_builtins()
        return self.db.query(ChatWorkflow).order_by(ChatWorkflow.name.asc()).all()

    def get(self, workflow_id: int) -> ChatWorkflow | None:
        return self.db.query(ChatWorkflow).filter(ChatWorkflow.id == workflow_id).first()

    def create(self, **values) -> ChatWorkflow:
        row = ChatWorkflow(**values)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, row: ChatWorkflow, **values) -> ChatWorkflow:
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = now_ny()
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, row: ChatWorkflow) -> None:
        self.db.delete(row)
        self.db.commit()
