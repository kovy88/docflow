import { test, expect } from "@playwright/test";

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

async function registerNewAccount(page: import("@playwright/test").Page, tag: string) {
  const email = uniqueEmail(tag);
  await page.goto("/register");
  await page.getByLabel("Company name").fill(`E2E ${tag} Co`);
  await page.getByLabel("Your name").fill(`E2E ${tag} Tester`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a-sufficiently-long-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
  return email;
}

test("wrong password on login shows an error and does not navigate away", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(uniqueEmail("nosuchuser"));
  await page.getByLabel("Password").fill("whatever-wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  // The login form's own error box, not a generic page crash.
  await expect(page.locator(".text-danger")).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveURL(/\/login$/);
});

test("registering the same email twice is rejected on the second attempt", async ({ page }) => {
  const email = uniqueEmail("dup");

  await page.goto("/register");
  await page.getByLabel("Company name").fill("Dup Test Co");
  await page.getByLabel("Your name").fill("Dup Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a-sufficiently-long-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  await page.goto("/register");
  await page.getByLabel("Company name").fill("Dup Test Co Two");
  await page.getByLabel("Your name").fill("Dup Tester Two");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a-different-long-password");
  await page.getByRole("button", { name: "Create workspace" }).click();

  // Rejected, not silently accepted as a second account — stays on the
  // register page with the API's error surfaced, not a blank failure.
  await expect(page).toHaveURL(/\/register$/);
  await expect(page.locator(".text-danger")).toBeVisible({ timeout: 10_000 });
});

test("a fresh account sees empty states, not errors or infinite spinners", async ({ page }) => {
  await registerNewAccount(page, "empty");

  await page.goto("/documents");
  await expect(page.getByText("No documents yet")).toBeVisible();

  await page.goto("/review");
  await expect(page.getByText("Nothing needs review")).toBeVisible();

  await page.goto("/dashboard");
  await expect(page.getByText("Total documents")).toBeVisible();
  await expect(page.getByText("0", { exact: true }).first()).toBeVisible();
});

test("signing out blocks access to authenticated pages until logging back in", async ({ page }) => {
  const email = await registerNewAccount(page, "logout");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  // Direct navigation to an authenticated route while logged out redirects
  // back to /login rather than rendering a broken/empty authenticated page.
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  // And logging back in with the same credentials works.
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a-sufficiently-long-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
});
