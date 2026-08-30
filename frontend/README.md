# MarketLens Frontend

React/TypeScript dashboard for the MarketLens market analysis platform.

## Setup

```bash
cd frontend
npm install
```

## Development

```bash
npm start
```

The app runs on `http://localhost:3000` and expects the API at `http://localhost:5001`.

## Configuration

Create a `.env` file (already included) to override the API URL:

```
REACT_APP_API_URL=http://localhost:5001/api
```

## Build

```bash
npm run build
```

### Bundle Analysis

To inspect the production bundle and find what's contributing to size:

```bash
npm run build:analyze
```

This runs `craco build` with the `webpack-bundle-analyzer` plugin enabled. It writes:

- `build/stats.html` — interactive treemap (open in browser)
- `build/stats.json` — raw stats file for further analysis

The plugin is configured in `craco.config.js` and only activates when `ANALYZE=true` is set, so regular `npm run build` is unaffected.

## Features

- **Dashboard** — Real-time market regime, trends, confluence, and strategy analysis
- **Watchlist Management** — Create/edit/delete watchlists, add/remove/reorder symbols
- **System Health** — Monitor API health, data ingestion, and service status
- **Auto-Refresh** — Optional 30-second polling for live data
- **Responsive** — Works on desktop and mobile

## Architecture

```
src/
├── App.tsx               # Root component with navigation
├── index.tsx             # React entry point
├── pages/
│   ├── Dashboard.tsx     # Market analysis page
│   ├── WatchlistPage.tsx # Watchlist management
│   └── SystemHealth.tsx  # System monitoring
├── components/           # Reusable UI components
│   ├── RegimeCard.tsx
│   ├── TrendCard.tsx
│   ├── ConfluenceCard.tsx
│   ├── StrategyCard.tsx
│   ├── SymbolInput.tsx
│   ├── LoadingSpinner.tsx
│   └── ErrorBanner.tsx
├── services/
│   └── api.ts            # Type-safe API client
└── styles/
    └── App.css           # Global styles (dark theme)
```
