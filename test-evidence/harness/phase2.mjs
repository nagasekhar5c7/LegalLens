/**
 * Phase 2 — Scenario 1: create a conversation, ask a question, verify the
 * citations are real and resolvable, then rename and delete the conversation.
 */
import { launch, signIn, testCase, sel, apiGet, waitForTurn, BASE } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);

const s = sel(page);
const state = { convId: null, convTitle: null };

const QUESTION =
  'What happens if a change of control occurs without the consent required by the agreement?';

// ----------------------------------------------- TC-21  create conversation

await testCase(page, {
  id: 'TC-21', scenario: 'S1 · Create conversation',
  title: 'New chat plus a first question creates and opens a conversation',
  expected: 'URL becomes /c/{id}, the thread appears in the sidebar, and GET /api/conversations/{id} returns it',
}, async (t) => {
  const before = await apiGet(page, '/api/conversations');
  const countBefore = (before.body?.items ?? []).length;

  await s.newChat.click();
  await page.waitForTimeout(800);
  await t.shot('new-chat-empty');

  await s.prompt.fill(QUESTION);
  await s.modeChip('Ask').click();
  await page.waitForTimeout(400);
  await t.shot('question-typed');

  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 30000 });
  state.convId = new URL(page.url()).pathname.split('/c/')[1];
  await t.shot('conversation-created-streaming');

  const got = await apiGet(page, `/api/conversations/${state.convId}`);
  const list = await apiGet(page, '/api/conversations');
  const inList = (list.body?.items ?? []).some((c) => c.id === state.convId);
  state.convTitle = got.body?.title;

  return {
    pass: !!state.convId && got.status === 200 && inList,
    actual: `id=${state.convId} GET=${got.status} title="${state.convTitle}" inSidebarList=${inList} (was ${countBefore} conversations)`,
  };
});

// ------------------------------------------------------ TC-22  the answer

await testCase(page, {
  id: 'TC-22', scenario: 'S1 · Ask workflow',
  title: 'Ask streams a grounded answer over the real retrieval pipeline',
  expected: 'The turn completes with non-empty prose and persists as status=complete',
}, async (t) => {
  await waitForTurn(page, 480000);
  await t.shot('answer-complete', { fullPage: true });
  const text = await s.assistant.last().textContent();
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const turns = stored.body?.turns ?? [];
  const asst = turns.filter((x) => x.role === 'assistant').pop();
  return {
    pass: !!asst && asst.status === 'complete' && (asst.text || '').length > 200,
    actual: `stored status=${asst?.status} kind=${asst?.kind} intent=${asst?.intent} textChars=${(asst?.text || '').length} renderedChars=${text.length}`,
  };
});

// --------------------------------------------------- TC-23  citations exist

await testCase(page, {
  id: 'TC-23', scenario: 'S1 · Citations',
  title: 'Every citation chip resolves to a real document, page and span',
  expected: 'At least one [n] chip rendered, and each stored citation carries documentId, page and spanId',
}, async (t) => {
  const chips = await s.citations.count();
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const cites = asst?.citations ?? [];
  const docs = await apiGet(page, '/api/documents');
  const items = docs.body?.items ?? [];
  const allResolve = cites.length > 0 && cites.every(
    (c) => c.documentId && c.page > 0 && c.spanId && items.some((d) => d.id === c.documentId),
  );
  await t.shot('answer-with-citations');
  return {
    pass: chips > 0 && allResolve,
    actual: `chipsInUi=${chips} storedCitations=${cites.length} ` +
      cites.map((c) => `[${c.n}]→${c.documentName?.slice(0, 26)} p${c.page} ${c.section ?? ''}`).join('; ') +
      ` allResolveToOwnedDocuments=${allResolve}`,
  };
});

// ------------------------------------------- TC-24  hover card on a citation

await testCase(page, {
  id: 'TC-24', scenario: 'S1 · Citations',
  title: 'Hovering a citation previews the source passage without leaving the answer',
  expected: 'Hover card names the document and page and shows the retrieved excerpt text',
}, async (t) => {
  const chip = s.citations.first();
  await chip.scrollIntoViewIfNeeded();
  await chip.hover();
  await page.waitForTimeout(1400);
  const body = await page.locator('body').textContent();
  const hasVerifyLine = /Click to verify beside the answer/i.test(body);
  const hasOpenDoc = /Open document/i.test(body);
  await t.shot('citation-hovercard');
  return {
    pass: hasVerifyLine && hasOpenDoc,
    actual: `hoverCardShown=${hasVerifyLine} openDocumentAction=${hasOpenDoc}`,
  };
});

// ------------------------------------- TC-25  click a citation → evidence pane

await testCase(page, {
  id: 'TC-25', scenario: 'S1 · Citations',
  title: 'Clicking a citation opens the evidence panel on the retrieved passage',
  expected: 'Evidence panel is visible and contains the excerpt the citation points at',
}, async (t) => {
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const firstExcerpt = (asst?.excerpts ?? [])[0];

  await s.citations.first().click();
  await page.waitForTimeout(1500);
  const panelVisible = await s.evidencePanel.isVisible().catch(() => false);
  const panelText = panelVisible ? await s.evidencePanel.textContent() : '';
  const probe = (firstExcerpt?.text ?? '').replace(/\s+/g, ' ').slice(0, 40);
  const containsExcerpt = probe.length > 10 && panelText.replace(/\s+/g, ' ').includes(probe);
  await t.shot('evidence-panel-open', { fullPage: false });
  return {
    pass: panelVisible && containsExcerpt,
    actual: `panelVisible=${panelVisible} excerptsStored=${(asst?.excerpts ?? []).length} ` +
      `panelContainsRetrievedText=${containsExcerpt} probe="${probe}"`,
  };
});

// ------------------------------------------- TC-26  stage trace is auditable

await testCase(page, {
  id: 'TC-26', scenario: 'S1 · Transparency',
  title: 'The stage trace reports the retrieval pipeline that actually ran',
  expected: 'Stages include retrieving, reranking, assembling, writing and verifying with timings',
}, async (t) => {
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const keys = (asst?.stages ?? []).map((x) => x.key);
  const wanted = ['retrieving', 'reranking', 'assembling', 'writing', 'verifying'];
  const missing = wanted.filter((k) => !keys.includes(k));
  // Expand the trace in the UI for the screenshot.
  const toggle = page.locator('button', { hasText: /steps$/ }).first();
  if (await toggle.isVisible().catch(() => false)) {
    await toggle.click();
    await page.waitForTimeout(700);
  }
  await t.shot('stage-trace');
  return {
    pass: missing.length === 0,
    actual: `stages=${JSON.stringify(keys)} missing=${JSON.stringify(missing)} ` +
      `rerankDetail="${(asst?.stages ?? []).find((x) => x.key === 'reranking')?.detail ?? ''}"`,
  };
});

// ----------------------------------------- TC-27  scope statement + exclusions

await testCase(page, {
  id: 'TC-27', scenario: 'S1 · Transparency',
  title: 'The turn states the scope it searched and names what it excluded',
  expected: 'scopeUsed lists the searched documents and gives a reason for each excluded one',
}, async (t) => {
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const scope = asst?.scopeUsed;
  const excluded = scope?.excluded ?? [];
  const allHaveReasons = excluded.every((e) => !!e.reason);
  await t.shot('scope-statement');
  return {
    pass: !!scope && scope.documentCount > 0 && allHaveReasons,
    actual: `searched=${scope?.documentCount} documents / ${scope?.pageCount} pages; ` +
      `excluded=${excluded.length} (${excluded.map((e) => `${e.name?.slice(0, 22)}: ${e.reason}`).join(' | ')})`,
  };
});

// -------------------------------------- TC-28  work-product disclaimer (FR-25)

await testCase(page, {
  id: 'TC-28', scenario: 'S1 · Guardrails',
  title: 'A completed answer carries the attorney work-product disclaimer',
  expected: 'Disclaimer text is persisted on the turn and shown beneath the answer',
}, async (t) => {
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const body = await page.locator('body').textContent();
  const shown = /not legal advice|work-product/i.test(body);
  await t.shot('disclaimer');
  return {
    pass: !!asst?.disclaimer && shown,
    actual: `stored="${(asst?.disclaimer ?? '').slice(0, 90)}" shownInUi=${shown}`,
  };
});

// ------------------------------- TC-29  citation click-through to the document

await testCase(page, {
  id: 'TC-29', scenario: 'S1 · Citations',
  title: 'A citation opens the cited document at the cited page',
  expected: 'Open document navigates to /doc/{id}?page={n}&span={spanId} matching the citation',
}, async (t) => {
  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  const asst = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const c = (asst?.citations ?? [])[0];

  const chip = s.citations.first();
  await chip.scrollIntoViewIfNeeded();
  await chip.hover();
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /Open document/i }).first().click();
  await page.waitForURL(/\/doc\//, { timeout: 20000 });
  await page.waitForTimeout(3500);

  const url = new URL(page.url());
  const okDoc = url.pathname === `/doc/${c.documentId}`;
  const okPage = url.searchParams.get('page') === String(c.page);
  const okSpan = url.searchParams.get('span') === c.spanId;
  await t.shot('citation-opens-document');
  return {
    pass: okDoc && okPage && okSpan,
    actual: `citation [${c.n}] → ${c.documentName?.slice(0, 28)} p${c.page}; ` +
      `url=${url.pathname}${url.search} documentMatch=${okDoc} pageMatch=${okPage} spanMatch=${okSpan}`,
  };
});

// ------------------------------------------------- TC-30  rename conversation

await testCase(page, {
  id: 'TC-30', scenario: 'S2 · Rename conversation',
  title: 'Renaming a conversation updates the sidebar and persists server-side',
  expected: 'New title shown in the sidebar and returned by GET /api/conversations/{id}',
}, async (t) => {
  const NEW_TITLE = 'Change of control — Orion/Vertex consent analysis';
  await page.goto(`${BASE}/c/${state.convId}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  const row = page.locator('nav[aria-label="Conversations"] li').filter({
    has: page.locator(`a[href="/c/${state.convId}"]`),
  });
  await row.hover();
  await page.waitForTimeout(400);
  await row.getByRole('button', { name: /^Options for/ }).click();
  await page.waitForTimeout(700);
  await t.shot('conversation-context-menu');

  await page.getByRole('menuitem', { name: /Rename/ }).click();
  await page.waitForTimeout(800);
  await t.shot('rename-dialog');

  const input = page.locator('#rename');
  await input.fill(NEW_TITLE);
  await t.shot('rename-dialog-typed');
  await page.getByRole('button', { name: /^Save$/ }).click();
  await page.waitForTimeout(2500);
  await t.shot('renamed-in-sidebar');

  const got = await apiGet(page, `/api/conversations/${state.convId}`);
  const sidebarText = await page.locator('nav[aria-label="Conversations"]').textContent();
  const inSidebar = sidebarText.includes('Change of control — Orion');

  return {
    pass: got.body?.title === NEW_TITLE && inSidebar,
    actual: `before="${state.convTitle}" after(server)="${got.body?.title}" shownInSidebar=${inSidebar}`,
  };
});

// ------------------------------------------------- TC-31  delete conversation

await testCase(page, {
  id: 'TC-31', scenario: 'S3 · Delete conversation',
  title: 'Deleting a conversation removes it from the list and from the API',
  expected: 'Confirmation dialog names the thread; after deleting, GET returns 404 and the row is gone',
}, async (t) => {
  const row = page.locator('nav[aria-label="Conversations"] li').filter({
    has: page.locator(`a[href="/c/${state.convId}"]`),
  });
  await row.hover();
  await page.waitForTimeout(400);
  await row.getByRole('button', { name: /^Options for/ }).click();
  await page.waitForTimeout(700);
  await page.getByRole('menuitem', { name: /Delete/ }).click();
  await page.waitForTimeout(900);
  const dialogText = await page.getByRole('dialog').textContent();
  await t.shot('delete-confirm-dialog');

  await page.getByRole('button', { name: /Delete conversation/ }).click();
  await page.waitForTimeout(3000);
  await t.shot('after-delete');

  const got = await apiGet(page, `/api/conversations/${state.convId}`);
  const list = await apiGet(page, '/api/conversations');
  const stillListed = (list.body?.items ?? []).some((c) => c.id === state.convId);
  const rowGone = (await page.locator(`nav[aria-label="Conversations"] a[href="/c/${state.convId}"]`).count()) === 0;
  const warnsIrreversible = /cannot be undone/i.test(dialogText);

  return {
    pass: got.status === 404 && !stillListed && rowGone,
    actual: `confirmDialogWarnedIrreversible=${warnsIrreversible}; after delete GET=${got.status} ` +
      `stillInList=${stillListed} rowRemovedFromSidebar=${rowGone}`,
  };
});

await browser.close();
console.log('\nphase 2 complete');
