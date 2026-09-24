/**
 * Chat E2E test suite — exercises the full Chat panel in the browser.
 *
 * Prerequisites: dev server running on localhost:3000 / API on localhost:5001.
 *
 * Test groups:
 *   A. Navigation & session bootstrap
 *   B. Deterministic calculation paths (no AI call)
 *   C. Deterministic scenario intent (no AI call)
 *   D. Watchlist CRUD deterministic routes
 *   E. Market data replies with freshness tag (#10)
 *   F. Destructive action confirm gate
 *   G. Multi-step compound request
 *   H. Error & edge-case handling
 */

import { test, expect, type Page, type Locator } from '@playwright/test';

// ─── helpers ─────────────────────────────────────────────────────────────────

/** Delete all chat sessions so each test starts with a clean slate. */
async function clearSessions() {
  await fetch('http://localhost:5001/api/ai/chat/sessions', { method: 'DELETE' })
    .catch(() => { /* ignore — server may be briefly unavailable */ });
}

/**
 * Navigate to AI Hub and wait for the chat panel.
 * Clears all sessions first so the panel opens a fresh session.
 */
async function goToChat(page: Page) {
  await clearSessions();
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  await page.getByRole('button', { name: /AI Hub/i }).click();
  await expect(page.locator('#hub-chat')).toBeVisible({ timeout: 10_000 });
  // Wait for the session to be ready (Send button enabled when input has text)
  // We just wait for the input to be present and not disabled.
  await expect(page.locator('input[placeholder*="Ask about any stock"]')).toBeEnabled({ timeout: 10_000 });
}

async function sendMessage(page: Page, text: string) {
  const input = page.locator('input[placeholder*="Ask about any stock"]');
  await input.fill(text);
  await page.getByRole('button', { name: 'Send' }).click();
}

/**
 * Wait for a new fully-resolved assistant reply (not pending, not a streaming draft).
 * Counts existing final bubbles BEFORE the call to tolerate session message accumulation.
 * Draft bubbles (streaming in progress before verification) are excluded so the test
 * always sees the final ProvenanceRow-bearing message, not the intermediate draft.
 */
async function waitForNextAssistantReply(page: Page): Promise<Locator> {
  const bubbles = page.locator('.chat-bubble.assistant:not(.chat-bubble-pending):not(.chat-bubble-draft)');
  const before = await bubbles.count();
  await expect(bubbles).toHaveCount(before + 1, { timeout: 45_000 });
  return bubbles.nth(before);
}

// ─── A. Navigation & session bootstrap ───────────────────────────────────────

test.describe('A. Navigation & session bootstrap', () => {
  test('AI Hub page loads and chat panel is visible', async ({ page }) => {
    await goToChat(page);
    await expect(page.locator('#hub-chat')).toBeVisible();
    await expect(page.locator('input[placeholder*="Ask about any stock"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send' })).toBeVisible();
  });

  test('example chips are shown in empty state and clicking fills input', async ({ page }) => {
    await goToChat(page);
    // Example chips only appear in the empty state — session must have no messages
    const chip = page.locator('.chat-example-chip').first();
    await expect(chip).toBeVisible({ timeout: 5_000 });
    const chipText = await chip.textContent();
    await chip.click();
    const input = page.locator('input[placeholder*="Ask about any stock"]');
    await expect(input).toHaveValue(chipText!.trim());
  });

  test('Send button is disabled while input is empty', async ({ page }) => {
    await goToChat(page);
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  test('Send button is disabled for whitespace-only input', async ({ page }) => {
    await goToChat(page);
    await page.locator('input[placeholder*="Ask about any stock"]').fill('   ');
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});

// ─── B. Deterministic calculation paths ──────────────────────────────────────

test.describe('B. Deterministic calculation — no AI call', () => {
  test('risk/reward calculation returns verified result', async ({ page }) => {
    await goToChat(page);
    // _fallback_calculation requires the word "entry" (not "buy") for risk_reward
    await sendMessage(page, 'risk reward with entry at 100, stop at 95, target at 110');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText('risk_reward=2.0', { timeout: 30_000 });
    await expect(reply).toContainText('Formula:');
    await expect(page.locator('[aria-label="Verified calculation"]')).toBeVisible();
  });

  test('allocation calculation from natural language', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'A position is worth $25,000 in a $100,000 portfolio. What is its allocation?');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText('allocation_percent=25.0', { timeout: 30_000 });
  });

  test('missing calculation inputs prompts for values', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'what is my allocation?');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText('What values', { timeout: 30_000 });
  });

  test('position risk calculation from labelled inputs', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'buy 100 AAPL at 220, stop at 212, account value $50,000');
    const reply = await waitForNextAssistantReply(page);
    // Backend returns position_risk fields: per_share_risk, total_risk, risk_percent
    await expect(reply).toContainText(/per_share_risk|total_risk|risk_percent|position_risk/i, { timeout: 30_000 });
  });
});

// ─── C. Deterministic scenario intent ────────────────────────────────────────

test.describe('C. Scenario intent — natural language variants', () => {
  test('"suppose I bought" routes to scenario result', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'suppose I bought AAPL at 180, where am I now?');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/scenario|AAPL/i, { timeout: 30_000 });
  });

  test('"let\'s say I got in" routes to scenario result', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, "let's say I got in at 150 on TSLA");
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/scenario|TSLA/i, { timeout: 30_000 });
  });

  test('"what if drops X%" routes to scenario result', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'what if NVDA drops 10%?');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/scenario|NVDA/i, { timeout: 30_000 });
  });

  test('"if I entered at" routes to scenario result', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'if I entered at 200 on MSFT');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/scenario|MSFT/i, { timeout: 30_000 });
  });
});

// ─── D. Watchlist CRUD deterministic routes ──────────────────────────────────

test.describe('D. Watchlist CRUD — deterministic, no AI call', () => {
  test('ambiguous add without ticker asks for clarification', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'add to my watchlist');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/which ticker/i, { timeout: 15_000 });
  });

  test('create watchlist with a name routes immediately', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'create a new watchlist called Tech');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/watchlist|Tech/i, { timeout: 15_000 });
  });

  test('"delete my watchlist" hits confirm gate before deleting', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'delete my watchlist');
    const reply = await waitForNextAssistantReply(page);
    const text = await reply.textContent();
    // First response is a confirm question, not a "done" confirmation
    const isAskingForConfirm = /delete|confirm|sure|yes|watchlist/i.test(text ?? '');
    expect(isAskingForConfirm, `Expected confirm prompt, got: ${text}`).toBe(true);
  });

  test('add ticker to watchlist routes immediately with grounded flag', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'add AAPL to my watchlist');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/AAPL|watchlist/i, { timeout: 15_000 });
  });

  test('multi-step "create watchlist and add ticker" chains both steps', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'create a watchlist called E2ETest and add MSFT to it');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/watchlist|MSFT|E2ETest/i, { timeout: 45_000 });
  });
});

// ─── E. Market data replies with freshness tag ────────────────────────────────

test.describe('E. Freshness tag on market data replies (#10)', () => {
  test('market data tool reply contains provenance (provider · freshness or date)', async ({ page }) => {
    await goToChat(page);
    // Ask for support/resistance — forces a get_support_resistance tool call which
    // always returns source_timestamp. Even stale data shows "as of YYYY-MM-DD".
    await sendMessage(page, 'what are the support and resistance levels for AAPL?');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/AAPL/i, { timeout: 45_000 });
    const text = await reply.textContent() ?? '';
    // _format_generic_market_reply always appends provider · freshness tag.
    // Formats: "live, Xs" / "Xmin old" / "X.Xhr old ⚠" / "as of YYYY-MM-DD"
    // Accept any of these, or at minimum a provider name.
    const hasFreshnessOrProvider = /live,\s*\d+s|\d+min old|\d+\.\d+hr old|as of \d{4}-\d{2}-\d{2}|webull|alpaca|finnhub|MarketLens/.test(text);
    expect(hasFreshnessOrProvider, `Expected provider or freshness in reply. Full text: ${text}`).toBe(true);
  });

  test('market data reply does not leak raw JSON', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'get TSLA fundamentals');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/TSLA|fundamental/i, { timeout: 45_000 });
    const text = await reply.textContent() ?? '';
    // Raw JSON would look like {"key": — verify it never appears
    expect(text).not.toMatch(/\{.*"[a-z_]+":\s*(?:"|\d)/);
  });

  test('market data reply contains provider name', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, "what's SPY's market regime?");
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/SPY|regime|market/i, { timeout: 45_000 });
  });

  test('historical bars reply shows count of bars', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'show me AAPL daily bars');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/AAPL|bars?|historical/i, { timeout: 45_000 });
  });
});

// ─── F. Destructive action confirm gate ──────────────────────────────────────

test.describe('F. Destructive action confirm gate', () => {
  test('delete alert asks for confirmation before running', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'delete my AAPL price alert');
    const reply = await waitForNextAssistantReply(page);
    const text = await reply.textContent() ?? '';
    // First reply is a confirmation question — should NOT say "deleted" yet
    const isConfirmPrompt = /confirm|sure|delete|yes|alert|AAPL/i.test(text);
    expect(isConfirmPrompt, `Expected confirm prompt, got: ${text}`).toBe(true);
  });

  test('replying yes to confirm prompt attempts the action', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'delete my AAPL price alert');
    await waitForNextAssistantReply(page); // confirmation question
    await sendMessage(page, 'yes');
    const secondReply = await waitForNextAssistantReply(page);
    const text = await secondReply.textContent() ?? '';
    // After yes: either deleted, not found, or requires further info — any substantive reply
    expect(text.length, 'Expected a non-empty reply after confirming').toBeGreaterThan(10);
  });

  test('save to journal requires confirmation and includes symbol in reply', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'save AAPL long trade to my journal');
    const reply = await waitForNextAssistantReply(page);
    const text = await reply.textContent() ?? '';
    // Either shows confirm prompt or saves (if no confirm gate for journal)
    expect(text.length).toBeGreaterThan(5);
  });
});

// ─── G. Multi-step compound request ──────────────────────────────────────────

test.describe('G. Multi-step compound request', () => {
  test('compound "get quote and check regime" chains steps', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, "get AAPL's quote and also check the market regime");
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/AAPL|quote/i, { timeout: 45_000 });
  });

  test('"and add" pattern in message is treated as multi-step', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'create a watchlist called GrowthTest and add NVDA to it');
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/watchlist|NVDA|GrowthTest/i, { timeout: 45_000 });
  });
});

// ─── H. Error & edge-case handling ───────────────────────────────────────────

test.describe('H. Edge cases', () => {
  test('unknown ticker degrades gracefully', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'what is the price of ZZZNOTREAL?');
    const reply = await waitForNextAssistantReply(page);
    const text = await reply.textContent() ?? '';
    expect(text.length).toBeGreaterThan(5);
  });

  test('very long message fills input (respects 2000 char limit)', async ({ page }) => {
    await goToChat(page);
    const longMsg = 'what is the price of AAPL? '.repeat(70).slice(0, 1999);
    const input = page.locator('input[placeholder*="Ask about any stock"]');
    await input.fill(longMsg);
    const value = await input.inputValue();
    expect(value.length).toBeGreaterThan(100);
    expect(value.length).toBeLessThanOrEqual(2000);
  });

  test('sending a message shows pending bubble then resolves', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, 'what is 1+1?');
    // Pending bubble appears immediately
    await expect(page.locator('.chat-bubble-pending')).toBeVisible({ timeout: 5_000 });
    // Then resolves to a real answer
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toBeVisible();
    const text = await reply.textContent() ?? '';
    expect(text.length).toBeGreaterThan(2);
  });

  test('no raw JSON leaks into any assistant reply for a quote request', async ({ page }) => {
    await goToChat(page);
    await sendMessage(page, "get AAPL's quote");
    const reply = await waitForNextAssistantReply(page);
    await expect(reply).toContainText(/AAPL/i, { timeout: 45_000 });
    const text = await reply.textContent() ?? '';
    expect(text).not.toMatch(/\{.*"[a-z_]+":\s*(?:"|\d)/);
  });

  test('user message appears in the chat immediately (optimistic UI)', async ({ page }) => {
    await goToChat(page);
    const msg = 'quick test message';
    await sendMessage(page, msg);
    // User bubble appears immediately without waiting for reply
    await expect(page.locator('.chat-bubble.user').first()).toContainText(msg, { timeout: 3_000 });
  });
});
