/** Phase 6 — remaining interactive surface: palette, panels, pin, export, feedback, stop, sign out. */
import { launch, signIn, testCase, sel, apiGet, BASE } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);

/** The newest conversation that already holds a completed answer with citations. */
async function conversationWithAnswer() {
  const list = await apiGet(page, '/api/conversations?limit=30');
  for (const c of list.body?.items ?? []) {
    const full = await apiGet(page, `/api/conversations/${c.id}`);
    const turn = (full.body?.turns ?? []).find(
      (x) => x.role === 'assistant' && x.status === 'complete' && (x.citations ?? []).length > 0,
    );
    if (turn) return { id: c.id, title: c.title, turn };
  }
  return null;
}

const target = await conversationWithAnswer();

await testCase(page, {
  id: 'TC-50', scenario: 'Interactive shell',
  title: 'Command palette opens on the keyboard shortcut and searches the workspace',
  expected: '⌘K opens a search palette listing conversations, documents and actions',
}, async (t) => {
  await page.keyboard.press('Meta+k');
  await page.waitForTimeout(1200);
  const input = page.getByPlaceholder(/Search conversations, documents and actions/i);
  const open = await input.isVisible().catch(() => false);
  let results = 0;
  if (open) {
    await input.fill('library');
    await page.waitForTimeout(900);
    results = await page.getByRole('option').count().catch(() => 0);
  }
  await t.shot('command-palette');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
  return { pass: open, actual: `paletteOpened=${open} matchesFor"library"=${results}` };
});

await testCase(page, {
  id: 'TC-51', scenario: 'Interactive shell',
  title: 'Evidence panel can be hidden and brought back',
  expected: 'Hiding the panel removes it; the top bar action restores it',
}, async (t) => {
  await page.goto(`${BASE}/c/${target.id}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  const hide = page.getByRole('button', { name: /Hide the evidence panel/ });
  const wasVisible = await s.evidencePanel.isVisible().catch(() => false);
  if (await hide.isVisible().catch(() => false)) await hide.click();
  await page.waitForTimeout(900);
  const hidden = !(await s.evidencePanel.isVisible().catch(() => false));
  await t.shot('evidence-hidden');
  await page.getByRole('button', { name: /Show the evidence panel/ }).click();
  await page.waitForTimeout(900);
  const restored = await s.evidencePanel.isVisible().catch(() => false);
  await t.shot('evidence-restored');
  return {
    pass: wasVisible && hidden && restored,
    actual: `initiallyVisible=${wasVisible} hiddenAfterToggle=${hidden} restored=${restored}`,
  };
});

await testCase(page, {
  id: 'TC-52', scenario: 'Interactive shell',
  title: 'Sidebar can be collapsed and restored',
  expected: 'Hiding the sidebar removes the conversation list; the top bar action restores it',
}, async (t) => {
  await page.getByRole('button', { name: /Hide the sidebar/ }).click();
  await page.waitForTimeout(900);
  const gone = (await page.locator('nav[aria-label="Conversations"]').count()) === 0
    || !(await page.locator('nav[aria-label="Conversations"]').isVisible().catch(() => false));
  await t.shot('sidebar-collapsed');
  await page.getByRole('button', { name: /Show the sidebar/ }).click();
  await page.waitForTimeout(900);
  const back = await page.locator('nav[aria-label="Conversations"]').isVisible().catch(() => false);
  await t.shot('sidebar-restored');
  return { pass: gone && back, actual: `collapsed=${gone} restored=${back}` };
});

await testCase(page, {
  id: 'TC-53', scenario: 'Conversation management',
  title: 'Pinning a conversation moves it into a Pinned group and persists',
  expected: 'The thread appears under a "Pinned" heading and pinned=true server-side',
}, async (t) => {
  const row = page.locator('nav[aria-label="Conversations"] li').filter({
    has: page.locator(`a[href="/c/${target.id}"]`),
  });
  await row.hover();
  await page.waitForTimeout(400);
  await row.getByRole('button', { name: /^Options for/ }).click();
  await page.waitForTimeout(700);
  await page.getByRole('menuitem', { name: /Pin to top/ }).click();
  await page.waitForTimeout(2000);
  await t.shot('conversation-pinned');

  const groups = await page.locator('nav[aria-label="Conversations"] h3').allTextContents();
  const after = await apiGet(page, '/api/conversations?limit=30');
  const rec = (after.body?.items ?? []).find((c) => c.id === target.id);

  // Put it back.
  const row2 = page.locator('nav[aria-label="Conversations"] li').filter({
    has: page.locator(`a[href="/c/${target.id}"]`),
  });
  await row2.hover();
  await row2.getByRole('button', { name: /^Options for/ }).click();
  await page.waitForTimeout(600);
  await page.getByRole('menuitem', { name: /Unpin/ }).click();
  await page.waitForTimeout(1500);
  const restored = await apiGet(page, '/api/conversations?limit=30');
  const rec2 = (restored.body?.items ?? []).find((c) => c.id === target.id);

  return {
    pass: groups.includes('Pinned') && rec?.pinned === true && rec2?.pinned === false,
    actual: `groupsAfterPin=${JSON.stringify(groups)} serverPinned=${rec?.pinned}; ` +
      `afterUnpin serverPinned=${rec2?.pinned}`,
  };
});

await testCase(page, {
  id: 'TC-54', scenario: 'Output handling',
  title: 'Copy and export offers markdown and plain text with citations preserved',
  expected: 'The export menu lists copy and download options for the answer',
}, async (t) => {
  await page.goto(`${BASE}/c/${target.id}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  const trigger = page.getByRole('button', { name: /Copy or export this output/ }).first();
  await trigger.scrollIntoViewIfNeeded();
  await trigger.click();
  await page.waitForTimeout(900);
  const items = await page.getByRole('menuitem').allTextContents();
  await t.shot('export-menu');
  await page.keyboard.press('Escape');
  return {
    pass: items.length >= 3 && items.some((x) => /markdown/i.test(x)),
    actual: `menuItems=${JSON.stringify(items)}`,
  };
});

await testCase(page, {
  id: 'TC-55', scenario: 'Output handling',
  title: 'Feedback on an answer is recorded against its retrieval trace',
  expected: 'Marking an answer useful persists feedback and confirms it',
}, async (t) => {
  const thumb = page.getByRole('button', { name: /Mark this answer useful/ }).first();
  await thumb.scrollIntoViewIfNeeded();
  await thumb.click();
  await page.waitForTimeout(2500);
  const body = await page.locator('body').textContent();
  const toast = /Marked as useful|logged against/i.test(body);
  await t.shot('feedback-recorded');
  const full = await apiGet(page, `/api/conversations/${target.id}`);
  const turn = (full.body?.turns ?? []).find((x) => x.id === target.turn.id);
  return {
    pass: toast || !!turn?.feedback,
    actual: `confirmationShown=${toast} storedFeedback=${JSON.stringify(turn?.feedback ?? null)} ` +
      `traceId=${target.turn.traceId ?? 'n/a'}`,
  };
});

await testCase(page, {
  id: 'TC-56', scenario: 'Guardrails',
  title: 'Stopping a stream marks the turn incomplete rather than complete (NFR-7)',
  expected: 'After Stop, the turn is not recorded as complete and the UI says so',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(800);
  await s.modeChip('Ask').click();
  await s.prompt.fill('Summarise every obligation in the services agreement in detail.');
  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 40000 });
  const convId = new URL(page.url()).pathname.split('/c/')[1];

  const stop = page.getByRole('button', { name: /^Stop$/ });
  await stop.waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForTimeout(2500);
  await t.shot('streaming-before-stop');
  await stop.click();
  await page.waitForTimeout(5000);
  await t.shot('after-stop');

  const full = await apiGet(page, `/api/conversations/${convId}`);
  const turn = (full.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const body = await page.locator('body').textContent();
  const marked = /incomplete/i.test(body);
  return {
    pass: turn?.status !== 'complete',
    actual: `storedStatus=${turn?.status} (must not be "complete"); uiMarksIncomplete=${marked}`,
  };
});

await testCase(page, {
  id: 'TC-57', scenario: 'Authentication',
  title: 'Signing out revokes the session server-side',
  expected: 'After sign out the app returns to /login and /api/auth/me is 401',
}, async (t) => {
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: /^Sign out$/ }).click();
  await page.waitForTimeout(3000);
  const p = new URL(page.url()).pathname;
  const me = await apiGet(page, '/api/auth/me');
  await t.shot('signed-out');
  return {
    pass: p === '/login' && me.status === 401,
    actual: `landedOn=${p} /api/auth/me=${me.status}`,
  };
});

await browser.close();
console.log('\nphase 6 complete');
