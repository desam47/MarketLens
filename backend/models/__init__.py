"""
Data models for MarketLens
"""
# Import alert models
from .alert import Alert as Alert
from .alert import AlertTrigger as AlertTrigger

# Import backtest models
from .backtest import BacktestRun as BacktestRun
from .backtest import BacktestTrade as BacktestTrade

# Import experiment models
from .experiment import Experiment as Experiment
from .market_data import (
    Bar as Bar,
)
from .market_data import (
    DataStatus as DataStatus,
)
from .market_data import (
    MarketStatus as MarketStatus,
)
from .market_data import (
    ProviderCapabilities as ProviderCapabilities,
)
from .market_data import (
    ProviderStatus as ProviderStatus,
)
from .market_data import (
    Quote as Quote,
)

# Import SQLAlchemy models
from .market_data_sql import (
    BarModel as BarModel,
)
from .market_data_sql import (
    MarketStatusModel as MarketStatusModel,
)
from .market_data_sql import (
    ProviderStatusModel as ProviderStatusModel,
)
from .market_data_sql import (
    QuoteModel as QuoteModel,
)

# Import signal models
from .signal import HistoricalSignal as HistoricalSignal

# Import watchlist models
from .watchlist import Watchlist as Watchlist
from .watchlist import WatchlistSymbol as WatchlistSymbol

# Import custom indicator models (Phase 2.3.4)
from .custom_indicator import CustomIndicator as CustomIndicator

# Import drawing tools models (Phase 2.3.5)
from .drawing import DrawingTool as DrawingTool

# Import AI template models (Phase 2.4.5)
from .ai_template import AITemplate as AITemplate

# Import AI analysis job models (Phase 2.5: background AI processing)
from .ai_analysis_job import AIAnalysisJob as AIAnalysisJob
