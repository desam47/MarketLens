import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import ChatWorkflow
from backend.repositories.workflow_repository import WorkflowRepository


def test_builtin_workflows_are_seeded_and_typed() -> None:
    engine = create_engine("sqlite:///:memory:")
    ChatWorkflow.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    try:
        rows = WorkflowRepository(db).list()
        assert {row.name for row in rows} == {
            "Morning Review",
            "Evaluate a Breakout",
            "Options Setup Review",
            "End-of-Day Journal Review",
        }
        for row in rows:
            assert isinstance(json.loads(row.steps), list)
            assert all("tool" in step and "requires_confirmation" in step for step in json.loads(row.steps))
    finally:
        db.close()
        engine.dispose()


def test_custom_workflow_can_be_updated_without_prompt_prose() -> None:
    engine = create_engine("sqlite:///:memory:")
    ChatWorkflow.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    try:
        repo = WorkflowRepository(db)
        row = repo.create(
            name="Custom Review",
            description="A typed review",
            steps=json.dumps([{"tool": "get_quote", "arguments": {"symbol": "AAPL"}, "requires_confirmation": False}]),
            parameters=json.dumps({"symbol": "AAPL"}),
            output_layout="summary",
            is_builtin=False,
        )
        repo.update(row, output_layout="evidence_table")
        assert repo.get(row.id).output_layout == "evidence_table"
    finally:
        db.close()
        engine.dispose()
