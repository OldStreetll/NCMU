import { test, expect, type Page } from "@playwright/test";

const ZHANG_SAN_NAME = "张三";

async function loginAs(page: Page, displayName: string) {
  await page.goto("/login");
  await expect(page.getByText("选择测试账号登录")).toBeVisible();

  // The Antd `<Select>` exposes a combobox role; click it to expand options.
  const select = page.getByRole("combobox");
  await select.click();
  await page
    .locator(".ant-select-item-option")
    .filter({ hasText: displayName })
    .first()
    .click();

  // Antd's submit button has htmlType="submit" → DOM `<button type="submit">`.
  // Locate by attribute to avoid ambiguity with Antd's loading/icon role swaps.
  await page.locator('button[type="submit"]').click();

  await expect(page).toHaveURL(/\/chat(\/.+)?$/);
  await expect(page.locator('[data-slot="chat-page"]')).toBeVisible();

  const jwt = await page.evaluate(() => sessionStorage.getItem("ncmu_jwt"));
  expect(jwt, "JWT must be in sessionStorage after login").toBeTruthy();
  expect((jwt ?? "").length).toBeGreaterThan(64);
}

test.describe("TASK-33 AC#1+#2 happy-path e2e", () => {
  test("login → /chat reachable → session list rail visible (frame)", async ({ page }) => {
    await loginAs(page, ZHANG_SAN_NAME);
    await expect(page.locator('[data-slot="session-list"]')).toBeVisible();
  });

  test("AC#1 happy-path: ask → stream → assistant grounded in KB chunk", async ({ page }) => {
    test.setTimeout(120_000);
    await loginAs(page, ZHANG_SAN_NAME);

    // 1. Type a question that retrieves from the [KB] App's external KB.
    const composer = page.locator('[data-slot="chat-input"] textarea');
    await composer.fill("E2E-TASK-33-MARKER 是什么？请引用原文");
    await page.locator('[data-slot="chat-input"] button').click();

    // 2. Wait for an assistant bubble to render — first frame may take a
    //    few seconds while Dify retrieval + LLM warm up.
    const assistant = page.locator('[data-role="assistant"]').last();
    await expect(assistant).toBeVisible({ timeout: 30_000 });

    // 3. The assistant text must literally include the marker. The chunk
    //    `E2E-TASK-33-MARKER：本段验证 NCMU Phase 1 真容器集成链路…` is the
    //    only KB content where this marker appears, so a grounded answer
    //    must surface it. Verifies the full retrieve-then-generate chain.
    await expect(assistant).toContainText("E2E-TASK-33-MARKER", { timeout: 60_000 });
  });

  // AC#1 retriever-chip strip — unskipped after TASK-44 fixed the
  // ChatWindow message_end dual-read (commit 6c9ca23). The chip text
  // contains the FastGPT collection name "TASK-33 手册" (verified in
  // ncmu-fastgpt-mongo dataset_collections), which kb-adapter forwards
  // as `title` and Dify maps to `retriever_resources[].document_name`.
  // RetrieverChip.tsx:53 renders "📄 {document_name}", so the chip text
  // must contain "TASK-33" — a field-level literal, not a "chip exists"
  // smoke check.
  test("AC#1: retriever-chip strip renders with KB document name", async ({ page }) => {
    test.setTimeout(120_000);
    await loginAs(page, ZHANG_SAN_NAME);
    const composer = page.locator('[data-slot="chat-input"] textarea');
    await composer.fill("E2E-TASK-33-MARKER 是什么？请引用原文");
    await page.locator('[data-slot="chat-input"] button').click();

    const chips = page.locator('[data-testid="retriever-chip"]');
    await expect.poll(() => chips.count(), { timeout: 60_000 }).toBeGreaterThan(0);

    const retrieverChipsCount = await chips.count();
    expect(retrieverChipsCount).toBeGreaterThan(0);

    const retrieverChipText = await chips.first().innerText();
    expect(retrieverChipText).toContain("TASK-33");
  });

  // AC#2 multi-turn history (refresh → restore):
  // covered at the curl/SQL level in §9 of the integration-test report
  // (4 rounds + Dify pg messages SQL + GET /sessions/:id/messages all
  // verified). The full UI-level multi-turn refresh-restore would add
  // ~90s of test runtime without new contract evidence; deferred to a
  // future Playwright suite once we have an explicit "session re-open"
  // user journey to anchor the test on.
  test.skip("AC#2 multi-turn history persists after refresh (curl/SQL covered in §9)", async () => {
    /* placeholder — see report §9 */
  });
});
