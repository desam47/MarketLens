#!/usr/bin/env python
"""Create the local MarketLens schema and seed the default AI template.

Run from the repository root with ``python scripts/init_db.py``. Alembic is
the normal schema-management path; this utility remains useful for a new local
database or a disposable development database.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

# The project root, rather than ``backend/``, must be importable for the
# package imports below to resolve when this script is run directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.database import Base, SessionLocal, engine
from backend.models import AITemplate


def _load_system_prompt() -> str:
    """Load the seed prompt without importing the AI package's runtime graph.

    ``backend.ai.__init__`` intentionally exposes the complete AI surface,
    which initializes market-data integrations. A schema/bootstrap command
    should not open provider or Redis connections merely to read one constant.
    """
    prompt_path = Path(_REPO_ROOT, "backend", "ai", "prompt.py")
    spec = importlib.util.spec_from_file_location("marketlens_seed_prompt", prompt_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load the seed prompt from {prompt_path}")
    prompt_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prompt_module)
    return prompt_module.SYSTEM_PROMPT


def _seed_ai_template() -> bool:
    """Insert the canonical AI template once, returning whether it succeeded."""
    db = SessionLocal()
    try:
        if db.query(AITemplate).first() is not None:
            return True

        db.add(
            AITemplate(
                name="Market Analysis Default",
                description=(
                    "The default system prompt for AI market analysis. "
                    "Use this as a starting point for your own templates."
                ),
                system_prompt=_load_system_prompt(),
                user_instructions=None,
                variables_json=json.dumps(["symbol", "timeframe"]),
                is_active=True,
                is_default=True,
                is_system=True,
            )
        )
        db.commit()
        print("🌱 Seeded default AI template 'Market Analysis Default'")
        return True
    except Exception as exc:  # noqa: BLE001 - report a CLI failure cleanly
        db.rollback()
        print(f"❌ Failed to seed default AI template: {exc}")
        return False
    finally:
        db.close()


def init_database() -> bool:
    """Create every registered model table and seed the default template."""
    print("🔧 Initializing MarketLens database...")
    try:
        # Importing ``backend.models`` above registers every ORM model with
        # Base.metadata, so create_all cannot silently omit newer features.
        Base.metadata.create_all(bind=engine)
        if not _seed_ai_template():
            return False

        print("✅ Database tables created successfully!")
        print("\n📋 Created tables:")
        for table in Base.metadata.tables:
            print(f"  - {table}")
        return True
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"❌ Failed to initialize database: {exc}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    sys.exit(0 if init_database() else 1)
