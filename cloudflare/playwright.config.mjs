import { defineConfig } from '@playwright/test';

const deployedURL = process.env.PLANNER_BROWSER_BASE_URL;
const baseURL = deployedURL || 'http://127.0.0.1:8789';

export default defineConfig({
  testDir: './browser-test',
  outputDir: '../.local/browser-tests',
  use: { baseURL, browserName: 'chromium', reducedMotion: 'reduce' },
  webServer: deployedURL ? undefined : {
    command: 'npm run dev -- --ip 127.0.0.1 --port 8789 --inspector-port 9236',
    url: baseURL + '/features',
    env: { WRANGLER_SEND_METRICS: 'false' },
  },
});
