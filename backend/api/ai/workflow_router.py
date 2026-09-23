"""Typed CRUD and safe preview execution for Chat workflows."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.ai.tool_registry import ToolRequest, default_registry
from backend.database import SessionLocal
from backend.repositories.workflow_repository import WorkflowRepository

router = APIRouter(prefix="/api/ai/workflows", tags=["ai-workflows"])


class WorkflowStep(BaseModel):
    tool: str = Field(..., min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False


class WorkflowPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    steps: list[WorkflowStep] = Field(..., min_length=1, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_layout: str = Field(default="summary", min_length=1, max_length=80)


class WorkflowResponse(WorkflowPayload):
    id: int
    is_builtin: bool
    created_at: str
    updated_at: str


def _response(row) -> WorkflowResponse:
    return WorkflowResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        steps=json.loads(row.steps),
        parameters=json.loads(row.parameters),
        output_layout=row.output_layout,
        is_builtin=bool(row.is_builtin),
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


@router.get("", response_model=list[WorkflowResponse])
def list_workflows():
    db = SessionLocal()
    try:
        return [_response(row) for row in WorkflowRepository(db).list()]
    finally:
        db.close()


@router.post("", response_model=WorkflowResponse)
def create_workflow(payload: WorkflowPayload):
    db = SessionLocal()
    try:
        row = WorkflowRepository(db).create(
            name=payload.name,
            description=payload.description,
            steps=json.dumps([step.model_dump() for step in payload.steps], sort_keys=True),
            parameters=json.dumps(payload.parameters, sort_keys=True),
            output_layout=payload.output_layout,
            is_builtin=False,
        )
        return _response(row)
    finally:
        db.close()


@router.put("/{workflow_id}", response_model=WorkflowResponse)
def update_workflow(workflow_id: int, payload: WorkflowPayload):
    db = SessionLocal()
    try:
        repo = WorkflowRepository(db)
        row = repo.get(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return _response(
            repo.update(
                row,
                name=payload.name,
                description=payload.description,
                steps=json.dumps([step.model_dump() for step in payload.steps], sort_keys=True),
                parameters=json.dumps(payload.parameters, sort_keys=True),
                output_layout=payload.output_layout,
            )
        )
    finally:
        db.close()


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: int):
    db = SessionLocal()
    try:
        repo = WorkflowRepository(db)
        row = repo.get(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        repo.delete(row)
    finally:
        db.close()


@router.post("/{workflow_id}/run")
def run_workflow(workflow_id: int, parameters: dict[str, Any] | None = None):
    db = SessionLocal()
    try:
        row = WorkflowRepository(db).get(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        merged = json.loads(row.parameters)
        merged.update(parameters or {})
        results = []
        for step in json.loads(row.steps):
            if step.get("requires_confirmation"):
                results.append({"tool": step["tool"], "status": "needs_confirmation"})
                continue
            arguments = json.loads(json.dumps(step.get("arguments", {})).replace(
                "{{symbol}}", str(merged.get("symbol", ""))
            ))
            result = default_registry.execute(ToolRequest(tool_name=step["tool"], arguments=arguments))
            results.append({"tool": step["tool"], "status": "completed" if result.ok else "failed", "data": result.data, "error": result.error})
        return {"workflow": _response(row), "parameters": merged, "results": results}
    finally:
        db.close()
