import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 40_000,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'edge-desktop', use: { ...devices['Desktop Edge'], channel: 'msedge', viewport: { width: 1440, height: 1000 } } }],
  // These tests use developer workspace tokens, so account sign-in is switched off for them.
  webServer: { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: true, timeout: 60_000, env: { VITE_ACCOUNT_SIGNIN: 'false' } },
});
