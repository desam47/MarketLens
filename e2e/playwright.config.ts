import path from 'path';
import { defineConfig, devices } from '@playwright/test';

// The suite runs against its own backend and frontend, never the dev servers
// on 5001/3000: its helper deletes every chat before each test, and it must
// not do that to real history. The backend gets a fresh SQLite file each run
// (migrated at startup), Redis off so no job reaches the live RQ workers
// (which write to the live database), and its own log directory.
const ROOT = path.resolve(__dirname, '..');
const STATE_DIR = path.join(ROOT, '.pytest_tmp', 'e2e');
const DB_FILE = path.join(STATE_DIR, 'e2e.db');
export const E2E_API_PORT = 5002;
export const E2E_WEB_PORT = 3002;

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'report', open: 'never' }]],
  use: {
    baseURL: `http://localhost:${E2E_WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command:
        `mkdir -p "${STATE_DIR}" && rm -f "${DB_FILE}" "${DB_FILE}-wal" "${DB_FILE}-shm" && ` +
        `python -m uvicorn backend.api.main:app --host 127.0.0.1 --port ${E2E_API_PORT}`,
      cwd: ROOT,
      env: {
        MARKETLENS_DB_OVERRIDE: `sqlite:///${DB_FILE}`,
        REDIS_ENABLED: 'false',
        WEBULL_STREAMING_ENABLED: 'false',
        MARKETLENS_LOG_DIR: path.join(STATE_DIR, 'logs'),
        // .env allows only the dev frontend's origin.
        CORS_ALLOWED_ORIGINS: JSON.stringify([`http://localhost:${E2E_WEB_PORT}`]),
      },
      url: `http://127.0.0.1:${E2E_API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: 'npm start',
      cwd: path.join(ROOT, 'frontend'),
      env: {
        PORT: String(E2E_WEB_PORT),
        BROWSER: 'none',
        REACT_APP_API_BASE_URL: `http://localhost:${E2E_API_PORT}/api`,
      },
      url: `http://localhost:${E2E_WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
