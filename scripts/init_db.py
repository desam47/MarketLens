#!/usr/bin/env python3
"""
Initialize the MarketLens database
Creates all tables defined in the SQLAlchemy models

Run from the repo root:  python3 scripts/init_db.py
The script imports `backend.*`, so the repo root must be on sys.path.
"""
import sys
import os

# Repo root is the parent of this script's directory. The `backend/`
# package lives at the repo root, so we need it (not the `backend/`
# dir itself) on sys.path for `from backend.database import ...` to
# resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from backend.database import engine, Base

def init_database():
    """Initialize the database by creating all tables"""
    print("🔧 Initializing MarketLens database...")

    try:
        # Load the market_data_sql module directly
        import importlib.util
        market_data_sql_spec = importlib.util.spec_from_file_location(
            "market_data_sql",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'market_data_sql.py')
        )
        market_data_sql = importlib.util.module_from_spec(market_data_sql_spec)
        market_data_sql_spec.loader.exec_module(market_data_sql)

        # Load the watchlist model directly
        watchlist_spec = importlib.util.spec_from_file_location(
            "watchlist",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'watchlist.py')
        )
        watchlist = importlib.util.module_from_spec(watchlist_spec)
        watchlist_spec.loader.exec_module(watchlist)

        # Load the alert model directly
        alert_spec = importlib.util.spec_from_file_location(
            "alert",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'alert.py')
        )
        alert = importlib.util.module_from_spec(alert_spec)
        alert_spec.loader.exec_module(alert)

        # Load the backtest model directly
        backtest_spec = importlib.util.spec_from_file_location(
            "backtest",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'backtest.py')
        )
        backtest = importlib.util.module_from_spec(backtest_spec)
        backtest_spec.loader.exec_module(backtest)

        # Load the signal model directly (Phase 13: HistoricalSignal)
        signal_spec = importlib.util.spec_from_file_location(
            "signal",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'signal.py')
        )
        signal = importlib.util.module_from_spec(signal_spec)
        signal_spec.loader.exec_module(signal)

        # Load Phase 2.3.4 model: custom_indicator
        custom_indicator_spec = importlib.util.spec_from_file_location(
            "custom_indicator",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'custom_indicator.py')
        )
        custom_indicator = importlib.util.module_from_spec(custom_indicator_spec)
        custom_indicator_spec.loader.exec_module(custom_indicator)

        # Load Phase 2.3.5 model: drawing
        drawing_spec = importlib.util.spec_from_file_location(
            "drawing",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'drawing.py')
        )
        drawing = importlib.util.module_from_spec(drawing_spec)
        drawing_spec.loader.exec_module(drawing)

        # Load Phase 2.4.5 model: ai_template
        ai_template_spec = importlib.util.spec_from_file_location(
            "ai_template",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'ai_template.py')
        )
        ai_template = importlib.util.module_from_spec(ai_template_spec)
        ai_template_spec.loader.exec_module(ai_template)

        # Load Phase 2.5 model: ai_analysis_job
        ai_analysis_job_spec = importlib.util.spec_from_file_location(
            "ai_analysis_job",
            os.path.join(_REPO_ROOT, 'backend', 'models', 'ai_analysis_job.py')
        )
        ai_analysis_job = importlib.util.module_from_spec(ai_analysis_job_spec)
        ai_analysis_job_spec.loader.exec_module(ai_analysis_job)

        # Get the model classes
        QuoteModel = market_data_sql.QuoteModel
        BarModel = market_data_sql.BarModel
        MarketStatusModel = market_data_sql.MarketStatusModel
        ProviderStatusModel = market_data_sql.ProviderStatusModel
        Watchlist = watchlist.Watchlist
        WatchlistSymbol = watchlist.WatchlistSymbol
        Alert = alert.Alert
        AlertTrigger = alert.AlertTrigger
        BacktestRun = backtest.BacktestRun
        BacktestTrade = backtest.BacktestTrade
        HistoricalSignal = signal.HistoricalSignal
        CustomIndicator = custom_indicator.CustomIndicator
        DrawingTool = drawing.DrawingTool
        AITemplate = ai_template.AITemplate
        AIAnalysisJob = ai_analysis_job.AIAnalysisJob

        # Create all tables
        Base.metadata.create_all(bind=engine)

        # Seed default AI template if none exist (Phase 2.4.5)
        _seed_ai_template(Base, engine)

        print("✅ Database tables created successfully!")
        print("\n📋 Created tables:")
        for table in Base.metadata.tables.keys():
            print(f"  - {table}")

        return True

    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        import traceback
        traceback.print_exc()
        return False


# --- Default template seeding ---------------------------------------------


def _seed_ai_template(Base, engine) -> None:
    """Insert the canonical "Market Analysis Default" template if missing.

    The body mirrors ``backend.ai.prompt.SYSTEM_PROMPT`` so users see a
    known-good starting point and can always reset. Idempotent — running
    init_db.py multiple times does not duplicate the row.
    """
    import json
    from datetime import datetime
    from backend.ai.prompt import SYSTEM_PROMPT

    SessionLocal = None
    try:
        # Lazy import: SessionLocal lives in backend.database.
        from backend.database import SessionLocal as _SessionLocal
        SessionLocal = _SessionLocal
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Could not seed default AI template: {e}")
        return

    db = SessionLocal()
    try:
        existing = db.query(AITemplate).first()
        if existing is not None:
            return  # User has at least one template; don't override.
        default = AITemplate(
            name="Market Analysis Default",
            description="The default system prompt for AI market analysis. "
                        "Use this as a starting point for your own templates.",
            system_prompt=SYSTEM_PROMPT,
            user_instructions=None,
            variables_json=json.dumps(["symbol", "timeframe"]),
            is_active=True,
            is_default=True,
            is_system=True,
        )
        db.add(default)
        db.commit()
        print("🌱 Seeded default AI template 'Market Analysis Default'")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"⚠️  Failed to seed default AI template: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)