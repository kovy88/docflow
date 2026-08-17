import { test, expect } from "@playwright/test";
import path from "node:path";

/**
 * The one flow the whole product exists to support: a new user registers,
 * uploads a document, watches it get processed, and reviews/corrects/approves
 * the result. Runs against the real stack with the fixture LLM provider
 * (deterministic, no network call) — see playwright.config.ts.
 */

const FIXTURE_INVOICE = path.join(__dirname, "fixtures", "test-invoice.txt");

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

test("register, upload, process, edit a field, and approve", async ({ page }) => {
  await registerNewAccount(page, "flow");

  // Dashboard landed on registration; nav to Documents and confirm the
  // fresh-account empty state (not a loading spinner stuck forever, not an
  // error).
  await page.getByRole("link", { name: "Documents" }).click();
  await expect(page).toHaveURL(/\/documents$/);
  await expect(page.getByText("No documents yet")).toBeVisible();

  // Upload — the dropzone's file input is visually hidden but still a real
  // input Playwright can target directly.
  await page.locator('input[type="file"]').setInputFiles(FIXTURE_INVOICE);

  // A successful upload redirects to the document detail page.
  await expect(page).toHaveURL(/\/documents\/[0-9a-fA-F-]+$/, { timeout: 15_000 });

  // Processing card shows while queued/processing, then disappears once the
  // fixture provider (near-instant) finishes and the extraction is ready.
  // (Not just "Confidence:" — the per-stage timeline also renders a debug
  // summary like "reasons: 3 · needs_review: true · overall_confidence:
  // 0.82", which contains the same substring.)
  await expect(page.getByText(/^Confidence: \d+%$/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Processing failed")).toHaveCount(0);

  // Edit a field: find the row by its exact field_path label, then the input
  // inside that same row.
  const invoiceNumberPath = page.getByText("invoice_number", { exact: true });
  await expect(invoiceNumberPath).toBeVisible();
  const invoiceNumberRow = invoiceNumberPath.locator(
    "xpath=ancestor::div[contains(@class, 'items-start')]",
  );
  const invoiceNumberInput = invoiceNumberRow.locator("input, textarea").first();
  await invoiceNumberInput.fill("E2E-2026-CORRECTED");

  // Saving a correction is a distinct step from approving — the UI disables
  // Approve while an edit is staged, specifically so a reviewer can't approve
  // mid-edit. Assert that gate, not just the happy path.
  const approveButton = page.getByRole("button", { name: /^Approve/ });
  await expect(approveButton).toBeDisabled();

  const saveButton = page.getByRole("button", { name: /^Save \d+ correction/ });
  await saveButton.click();
  await expect(page.getByText(/field\(s\) corrected/)).toBeVisible({ timeout: 10_000 });

  // Corrected value persisted and is now shown as the field's value.
  await expect(invoiceNumberInput).toHaveValue("E2E-2026-CORRECTED");

  // Approve.
  await expect(approveButton).toBeEnabled();
  await approveButton.click();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible({ timeout: 10_000 });

  // Once approved, the panel is read-only — editing controls disappear.
  await expect(page.getByRole("button", { name: /^Approve/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reject" })).toHaveCount(0);
});
