"""
API endpoints for AI prompt templates (Phase 2.4.5).

CRUD for user-defined system-prompt templates. Templates are global
(no per-user concept yet) and may be selected at request time when
calling ``POST /api/ai/analyze``.

A "Market Analysis Default" template is seeded by ``init_db.py`` and
mirrors the existing ``SYSTEM_PROMPT`` constant so users have a
known-good starting point.

Security:
- ``system_prompt`` length is capped at ``MAX_SYSTEM_PROMPT_LEN``.
- A simple keyword filter rejects obvious prompt-injection attempts.
- The seeded default (``is_system=True``) cannot be deleted.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.ai.prompt import render_template
from backend.database import get_db
from backend.models import AITemplate
from backend.models.ai_template import (
    MAX_DESCRIPTION_LEN,
    MAX_NAME_LEN,
    MAX_SYSTEM_PROMPT_LEN,
    MAX_USER_INSTRUCTIONS_LEN,
    PROMPT_INJECTION_KEYWORDS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/templates", tags=["ai-templates"])


# ── Pydantic schemas ──────────────────────────────────────────────────────

class AITemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    system_prompt: str = Field(..., min_length=10, max_length=MAX_SYSTEM_PROMPT_LEN)
    user_instructions: Optional[str] = Field(default=None, max_length=MAX_USER_INSTRUCTIONS_LEN)
    variables: list[str] = Field(default_factory=lambda: ["symbol", "timeframe"])
    is_active: bool = True
    is_default: bool = False


class AITemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    system_prompt: Optional[str] = Field(
        default=None, min_length=10, max_length=MAX_SYSTEM_PROMPT_LEN
    )
    user_instructions: Optional[str] = Field(default=None, max_length=MAX_USER_INSTRUCTIONS_LEN)
    variables: Optional[list[str]] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class AITemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    system_prompt: str
    user_instructions: Optional[str]
    variables: list[str]
    is_active: bool
    is_default: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class TemplatePreviewResponse(BaseModel):
    """Rendered template preview — what the AI would see, no AI call."""
    template_id: int
    system_prompt_rendered: str
    variables_used: dict[str, Any]
    missing_variables: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_variables(json_str: Optional[str]) -> list[str]:
    if not json_str:
        return []
    try:
        v = json.loads(json_str)
        return list(v) if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _to_response(model: AITemplate) -> AITemplateResponse:
    return AITemplateResponse(
        id=model.id,
        name=model.name,
        description=model.description,
        system_prompt=model.system_prompt,
        user_instructions=model.user_instructions,
        variables=_parse_variables(model.variables_json),
        is_active=model.is_active,
        is_default=model.is_default,
        is_system=model.is_system,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _check_injection(system_prompt: str) -> None:
    """Reject obvious prompt-injection attempts. Raises 400."""
    lower = system_prompt.lower()
    for kw in PROMPT_INJECTION_KEYWORDS:
        if kw in lower:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"system_prompt contains disallowed phrase: '{kw}'. "
                    "Templates must not attempt to override or rewrite prior instructions."
                ),
            )


def _validate_variables(variables: list[str]) -> None:
    if not isinstance(variables, list):
        raise HTTPException(status_code=400, detail="variables must be a list of strings")
    for v in variables:
        if not isinstance(v, str) or not v.replace("_", "").isalnum():
            raise HTTPException(
                status_code=400,
                detail=f"invalid variable name '{v}': must be alphanumeric (underscore ok)",
            )


def _ensure_single_default(db: Session, new_default_id: int) -> None:
    """When promoting a template to default, demote any others."""
    db.query(AITemplate).filter(AITemplate.id != new_default_id).update(
        {AITemplate.is_default: False}
    )


def _resolve_template_for_request(
    db: Session, template_id: Optional[int]
) -> Optional[AITemplate]:
    """Pick the template to use for an analysis call.

    - If ``template_id`` given, load it (must be active).
    - Else, fall back to the default template (if any).
    - Else, return None (caller uses the system default prompt).
    """
    if template_id is not None:
        tmpl = db.query(AITemplate).filter(AITemplate.id == template_id).first()
        if not tmpl:
            raise HTTPException(status_code=404, detail=f"AI template {template_id} not found")
        if not tmpl.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"AI template {template_id} is inactive",
            )
        return tmpl
    # No explicit id — use the default if one exists.
    return db.query(AITemplate).filter(
        AITemplate.is_default == True,  # noqa: E712
        AITemplate.is_active == True,   # noqa: E712
    ).first()


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[AITemplateResponse])
def list_templates(
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """List all AI templates. By default includes inactive (admin view)."""
    q = db.query(AITemplate)
    if active_only:
        q = q.filter(AITemplate.is_active == True)  # noqa: E712
    items = q.order_by(AITemplate.is_default.desc(), AITemplate.name).all()
    return [_to_response(t) for t in items]


@router.post("", response_model=AITemplateResponse, status_code=201)
def create_template(payload: AITemplateCreate, db: Session = Depends(get_db)):
    """Create a new AI template."""
    _check_injection(payload.system_prompt)
    _validate_variables(payload.variables)

    # Name uniqueness check (case-insensitive).
    existing = db.query(AITemplate).filter(AITemplate.name.ilike(payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"name '{payload.name}' already in use")

    if payload.is_default:
        _ensure_single_default(db, new_default_id=-1)  # -1 means "any id != existing"

    template = AITemplate(
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        user_instructions=payload.user_instructions,
        variables_json=json.dumps(payload.variables),
        is_active=payload.is_active,
        is_default=payload.is_default,
        is_system=False,  # Only seeded by init_db
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    if template.is_default:
        _ensure_single_default(db, new_default_id=template.id)
        db.commit()

    return _to_response(template)


@router.get("/default", response_model=AITemplateResponse)
def get_default_template(db: Session = Depends(get_db)):
    """Return the active default template, or 404 if none is set."""
    tmpl = db.query(AITemplate).filter(
        AITemplate.is_default == True,  # noqa: E712
        AITemplate.is_active == True,   # noqa: E712
    ).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="No default template configured")
    return _to_response(tmpl)


@router.get("/{template_id}", response_model=AITemplateResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    """Get a single template by ID."""
    tmpl = db.query(AITemplate).filter(AITemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return _to_response(tmpl)


@router.get("/{template_id}/preview", response_model=TemplatePreviewResponse)
def preview_template(
    template_id: int,
    symbol: str = Query(..., min_length=1, max_length=10),
    timeframe: str = Query(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$"),
    db: Session = Depends(get_db),
):
    """Render the template with the given context values.

    No AI call is made — this is purely a "show me what the model
    would see" preview so users can iterate on templates safely.
    """
    tmpl = db.query(AITemplate).filter(AITemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")

    variables = _parse_variables(tmpl.variables_json)
    # Built-in context always available; user-listed variables fall back to empty string.
    context: dict[str, Any] = {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
    }
    for v in variables:
        context.setdefault(v, "")

    rendered = render_template(tmpl.system_prompt, context)

    # Detect variables that appeared in the template but weren't supplied.
    import re
    used = re.findall(r"\{\{(\w+)\}\}", tmpl.system_prompt)
    missing = [name for name in used if name not in context or context.get(name) in (None, "")]

    return TemplatePreviewResponse(
        template_id=template_id,
        system_prompt_rendered=rendered,
        variables_used=context,
        missing_variables=sorted(set(missing)),
    )


@router.patch("/{template_id}", response_model=AITemplateResponse)
def update_template(
    template_id: int,
    payload: AITemplateUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing template. Only provided fields are changed."""
    tmpl = db.query(AITemplate).filter(AITemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "system_prompt" in update_data and update_data["system_prompt"] is not None:
        _check_injection(update_data["system_prompt"])

    if "variables" in update_data and update_data["variables"] is not None:
        _validate_variables(update_data["variables"])
        update_data["variables_json"] = json.dumps(update_data["variables"])
        update_data.pop("variables")

    if "name" in update_data and update_data["name"] is not None:
        clash = (
            db.query(AITemplate)
            .filter(AITemplate.id != template_id, AITemplate.name.ilike(update_data["name"]))
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409, detail=f"name '{update_data['name']}' already in use"
            )

    for field, value in update_data.items():
        setattr(tmpl, field, value)
    db.commit()
    db.refresh(tmpl)

    if tmpl.is_default:
        _ensure_single_default(db, new_default_id=template_id)
        db.commit()
        db.refresh(tmpl)

    return _to_response(tmpl)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Permanently delete a template. The system-seeded default is protected."""
    tmpl = db.query(AITemplate).filter(AITemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if tmpl.is_system:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the system-seeded default template. "
                   "Deactivate it via PATCH /api/ai/templates/{id} instead.",
        )
    db.delete(tmpl)
    db.commit()
    return None


# ── Internal helper exported for the AI analyze router ────────────────────


def resolve_and_render(
    db: Session,
    template_id: Optional[int],
    context: dict[str, Any],
) -> Optional[str]:
    """Resolve a template (if any) and render it with the given context.

    Returns the rendered system prompt, or ``None`` if no template
    applies (in which case the caller should fall back to the
    hard-coded ``SYSTEM_PROMPT``).

    Raises ``HTTPException(400)`` if the template's declared variables
    don't cover what the context supplies, or if any required
    variable is missing.
    """
    tmpl = _resolve_template_for_request(db, template_id)
    if tmpl is None:
        return None

    declared = set(_parse_variables(tmpl.variables_json))
    supplied = set(context.keys())
    missing_in_context = declared - supplied
    if missing_in_context:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Template {tmpl.id} requires variables not in the analysis "
                f"context: {sorted(missing_in_context)}. Add them to the "
                "analyze request or update the template."
            ),
        )

    # Variables with None values render as empty string — not an error,
    # the user might have set them deliberately blank.
    safe_context = {k: ("" if v is None else v) for k, v in context.items()}
    return render_template(tmpl.system_prompt, safe_context)
