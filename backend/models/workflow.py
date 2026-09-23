"""Persisted, typed Chat workflows (Phase 5.3.7).

Workflow steps are data, not hidden prompt prose: each step names one
registered tool, its editable arguments, and whether confirmation is required.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from backend.database import Base
from backend.utils.timezone import now_ny


class ChatWorkflow(Base):
    __tablename__ = "chat_workflows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    steps = Column(Text, nullable=False)
    parameters = Column(Text, nullable=False, default="{}")
    output_layout = Column(Text, nullable=False, default="summary")
    is_builtin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)
