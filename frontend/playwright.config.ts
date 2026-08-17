import { defineConfig, devices } from "@playwright/test";

/**
 * Runs against the real stack (Postgres, Redis, API, worker, frontend), not
 * mocked network calls — same philosophy as the backend's integration tests
 * (real DB, only the LLM provider substituted). The `webServer` command below
 * assumes the fixture LLM provider (docker-compose.yml's default), so
 * uploads process in milliseconds and cost nothing — see docs/AI.md.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "docker compose --profile full up -d",
    cwd: "..",
    url: "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
