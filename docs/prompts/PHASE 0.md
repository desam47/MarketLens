# PHASE 0 — Master rules

You are building a serious modular market-intelligence and quantitative research platform.

Before writing any code, read and understand these requirements.

PROJECT PURPOSE

Build a local-first, provider-agnostic market analysis platform that monitors a user-defined equity watchlist and determines directional trend across multiple timeframes.

The platform is for:

- market research

- technical analysis

- trend detection

- multi-timeframe analysis

- market scanning

- ranking

- alerts

- historical analysis

- backtesting

- optional AI-assisted interpretation

The platform must NOT execute trades.

ARCHITECTURAL PRINCIPLES

1. Market-data providers must be abstracted.

2. Webull must NOT be the default provider.

3. The initial system should work with free market data.

4. Webull must be addable later without rewriting the analysis engine.

5. AI must be optional.

6. The application must work completely without AI.

7. AI providers must be abstracted.

8. The core quantitative engine must not depend on AI.

9. Never hard-code API keys.

10. Never hard-code the user's watchlist.

11. Never hard-code timeframe weights.

12. Never hard-code indicator parameters.

13. Every important signal must be reproducible and versioned.

14. Historical calculations must not use future information.

15. Data quality must be validated before analysis.

16. Prefer incremental calculations over full recalculation.

17. Build interfaces before implementations.

18. Keep modules independently testable.

19. Avoid giant files and giant classes.

20. Avoid unnecessary dependencies.

CORE ARCHITECTURE

Data Provider

→ Provider Adapter

→ Data Normalization

→ Market Data Model

→ Timeframe Engine

→ Indicator Engine

→ Market Structure

→ Trend Engine

→ Multi-Timeframe Engine

→ Market Regime

→ Ranking/Scanner

→ Alerts

→ Optional AI

→ Frontend

AI must never replace the quantitative engine.

IMPLEMENTATION RULE

Work phase by phase.

Do not implement future phases early.

At the end of each phase:

1. Run tests.

2. Run lint/type checks.

3. Start the application where appropriate.

4. Verify functionality.

5. Fix all errors.

6. Summarize what was implemented.

7. List files changed.

8. List remaining known issues.

9. STOP and wait for the next phase prompt.

Do not proceed automatically.

First inspect the development environment and existing repository.

Do not modify anything yet.

Report:

- OS

- Python version

- Node version

- package managers

- Docker availability

- existing files

- existing project structure

- detected constraints

Then wait.