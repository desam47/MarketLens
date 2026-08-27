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

        # Create all tables
        Base.metadata.create_all(bind=engine)

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

if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)