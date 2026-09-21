"""
AI prompt template models for MarketLens (Phase 2.4.5).

Users can save custom system-prompt templates for AI analyses. Templates
support ``{{variable}}`` substitution (e.g. ``{{symbol}}``, ``{{timeframe}}``)
which are filled in from the analysis context at request time.

The "Market Analysis Default" template is seeded on first use and
mirrors the existing ``SYSTEM_PROMPT`` constant in ``backend.ai.prompt``,
so users always have a known-good starting point.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# Hard cap on the system-prompt body. Anything longer is almost
# certainly an attempt to bloat the model context or hide prompt
# injection, so we reject at the API layer before persisting.
MAX_SYSTEM_PROMPT_LEN = 10_000
MAX_USER_INSTRUCTIONS_LEN = 4_000
MAX_NAME_LEN = 100
MAX_DESCRIPTION_LEN = 500

# Simple keyword list for prompt-injection detection. The router
# rejects templates containing any of these substrings with HTTP 400.
# Not a guarantee — sophisticated attacks can bypass this — but
# cheap and catches the obvious cases.
PROMPT_INJECTION_KEYWORDS = (
    "ignore previous",
    "ignore all",
    "ignore instructions",
    "ignore the above",
    "disregard previous",
    "disregard all",
    "forget previous",
    "forget all",
    "system:",
    "you are now",
    "act as",
    "jailbreak",
)


class AITemplate(Base):
    """A saved AI system-prompt template.

    Templates are global (no per-user concept yet) and can be either
    user-created or the seeded system default. The ``is_default`` flag
    marks the canonical fallback when no template is selected.
    """

    __tablename__ = "ai_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list of variable names that appear as {{name}} in the system
    # prompt. Validated against the analysis context at request time.
    # Stored as a JSON-encoded string for SQLite compatibility.
    variables_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # True for the system-seeded template; prevents deletion.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<AITemplate(id={self.id}, name='{self.name}', is_default={self.is_default})>"
