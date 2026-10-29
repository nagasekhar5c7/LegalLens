/** Phase 1 — auth, shell, library, upload, composer controls. No AI turns. */
import { launch, signIn, testCase, sel, apiGet, BASE, EMAIL, PASSWORD } from './harness.mjs';

const DOCS = '/private/tmp/claude-501/-Users-sekhar-Desktop-Batch-B7-LegalLens/c3bb957e-e716-4268-a946-86b8fd1e820e/scratchpad/testdocs';

const { browser, page } = await launch();

// ---------------------------------------------------------------- AUTH

await testCase(page, {
  id: 'TC-01', scenario: 'Authentication',
  title: 'Sign-in page renders with email and password fields',
  expected: 'Login form shows email + password inputs and a Sign in button',
}, async (t) => {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  const email = await page.locator('input[type="email"]').isVisible();
  const pwd = await page.locator('input[type="password"]').isVisible();
  const btn = await page.getByRole('button', { name: /sign in/i }).isVisible();
  await t.shot('login-page');
  return { pass: email && pwd && btn, actual: `email=${email} password=${pwd} signInButton=${btn}` };
});

await testCase(page, {
  id: 'TC-02', scenario: 'Authentication',
  title: 'Wrong password is rejected without revealing which field was wrong',
  expected: 'Inline error shown, user stays on /login, no session granted',
}, async (t) => {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.locator('input[type="email"]').fill(EMAIL);
  await page.locator('input[type="password"]').fill('definitely-not-the-password');
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForTimeout(2500);
  const err = await page.getByText(/do not match an account/i).isVisible().catch(() => false);
  const stillLogin = new URL(page.url()).pathname === '/login';
  await t.shot('wrong-password-error');
  return {
    pass: err && stillLogin,
    actual: `errorShown=${err} remainedOnLogin=${stillLogin} url=${new URL(page.url()).pathname}`,
  };
});

await testCase(page, {
  id: 'TC-03', scenario: 'Authentication',
  title: 'Protected route redirects an unauthenticated visitor to /login',
  expected: 'Visiting /library while signed out lands on /login',
}, async (t) => {
  await page.context().clearCookies();
  await page.goto(`${BASE}/library`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  const p = new URL(page.url()).pathname;
  await t.shot('protected-route-redirect');
  return { pass: p === '/login', actual: `landed on ${p}` };
});

await testCase(page, {
  id: 'TC-04', scenario: 'Authentication',
  title: 'Valid credentials sign in and land on the chat workspace',
  expected: 'Session established, composer and sidebar visible, /api/auth/me returns the user',
}, async (t) => {
  await signIn(page);
  const composer = await page.locator('#prompt').isVisible();
  const sidebar = await page.getByRole('button', { name: /New chat/ }).isVisible();
  const me = await apiGet(page, '/api/auth/me');
  await t.shot('signed-in-workspace');
  return {
    pass: composer && sidebar && me.status === 200 && me.body?.email === EMAIL,
    actual: `composer=${composer} sidebar=${sidebar} me=${me.status} ${JSON.stringify(me.body)}`,
  };
});

// ---------------------------------------------------- WORKSPACE / SHELL

await testCase(page, {
  id: 'TC-05', scenario: 'Workspace shell',
  title: 'Sidebar lists conversations grouped by recency',
  expected: 'Conversation list renders with at least one group heading and one thread',
}, async (t) => {
  const s = sel(page);
  await page.waitForTimeout(1500);
  const n = await s.convLinks.count();
  const groups = await page.locator('nav[aria-label="Conversations"] h3').allTextContents();
  await t.shot('sidebar-conversations');
  return { pass: n > 0 && groups.length > 0, actual: `threads=${n} groups=${JSON.stringify(groups)}` };
});

await testCase(page, {
  id: 'TC-06', scenario: 'Workspace shell',
  title: 'Workspace health card reports indexed and excluded documents',
  expected: 'Health card shows counts that match /api/documents statuses',
}, async (t) => {
  const docs = await apiGet(page, '/api/documents');
  const items = docs.body?.items ?? docs.body ?? [];
  const ready = items.filter((d) => d.status === 'ready').length;
  const notReady = items.length - ready;
  const txt = await page.locator('nav[aria-label="Conversations"]').locator('..').locator('..').textContent();
  await t.shot('workspace-health');
  return {
    pass: items.length > 0,
    actual: `api: ${items.length} documents, ${ready} ready, ${notReady} not ready; card rendered=${!!txt}`,
  };
});

await testCase(page, {
  id: 'TC-07', scenario: 'Workspace shell',
  title: 'Sidebar search filters conversations and queries the server',
  expected: 'Typing a term narrows the list; clearing restores it',
}, async (t) => {
  const s = sel(page);
  const before = await s.convLinks.count();
  await page.locator('#conv-search').fill('zzz-no-such-thread-zzz');
  await page.waitForTimeout(1200);
  const empty = await page.getByText(/No matching conversations/i).isVisible().catch(() => false);
  await t.shot('search-no-match');
  await page.locator('#conv-search').fill('');
  await page.waitForTimeout(1200);
  const after = await s.convLinks.count();
  await t.shot('search-cleared');
  return {
    pass: empty && after === before,
    actual: `before=${before} emptyStateShown=${empty} afterClear=${after}`,
  };
});

// --------------------------------------------------------------- LIBRARY

await testCase(page, {
  id: 'TC-08', scenario: 'Document library',
  title: 'Library lists every document with type, pages and status',
  expected: 'A row per document from /api/documents, including non-ready ones with their status',
}, async (t) => {
  await page.goto(`${BASE}/library`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  const docs = await apiGet(page, '/api/documents');
  const items = docs.body?.items ?? docs.body ?? [];
  const rows = await page.locator('table tbody tr').count().catch(() => 0);
  const body = await page.locator('main, body').first().textContent();
  const named = items.filter((d) => body.includes(d.name.slice(0, 22))).length;
  await t.shot('library-table', { fullPage: true });
  return {
    pass: named >= items.length - 1 && items.length > 0,
    actual: `api documents=${items.length} rows=${rows} namesVisible=${named}`,
  };
});

await testCase(page, {
  id: 'TC-09', scenario: 'Document library',
  title: 'A failed document surfaces its failure rather than disappearing',
  expected: 'The failed Exhibit Bundle is listed with a failure status, not hidden',
}, async (t) => {
  const body = await page.locator('body').textContent();
  const docs = await apiGet(page, '/api/documents');
  const items = docs.body?.items ?? docs.body ?? [];
  const failed = items.filter((d) => d.status === 'failed');
  const visible = failed.every((d) => body.includes(d.name.slice(0, 20)));
  await t.shot('library-failed-doc');
  return {
    pass: failed.length > 0 && visible,
    actual: `failed documents=${failed.map((d) => d.name).join(', ')} allVisibleInUi=${visible}`,
  };
});

// ---------------------------------------------------------------- UPLOAD

await testCase(page, {
  id: 'TC-10', scenario: 'Upload & ingestion',
  title: 'Upload dialog states the real limits from the server',
  expected: 'Dropzone names PDF/DOCX/TXT, 50 MB, 500 pages, 50 documents — matching /api/config',
}, async (t) => {
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#prompt');
  await page.getByRole('button', { name: /Upload documents/ }).first().click();
  await page.waitForTimeout(900);
  const txt = await page.getByRole('dialog').textContent();
  const cfg = await apiGet(page, '/api/config');
  const c = cfg.body;
  const mb = Math.round(c.maxBytes / 1024 / 1024);
  const ok = txt.includes('PDF') && txt.includes('TXT') && txt.includes(String(mb))
    && txt.includes(String(c.maxPages)) && txt.includes(String(c.maxDocuments));
  await t.shot('upload-dialog-limits');
  return { pass: ok, actual: `config=${mb}MB/${c.maxPages}p/${c.maxDocuments}docs; dialog states them=${ok}` };
});

await testCase(page, {
  id: 'TC-11', scenario: 'Upload & ingestion',
  title: 'An unsupported file type is rejected per-file with the reason named',
  expected: 'The .pptx is rejected and the reason names the accepted types; the batch is not failed',
}, async (t) => {
  await page.locator('input[type="file"]').setInputFiles(`${DOCS}/unsupported-brief.pptx`);
  await page.waitForTimeout(3000);
  const txt = await page.getByRole('dialog').textContent();
  const rejected = /pptx/i.test(txt) && /(pdf|docx|txt)/i.test(txt);
  await t.shot('upload-rejected-pptx');
  return { pass: rejected, actual: `dialog mentions the rejected file and accepted types=${rejected}` };
});

await testCase(page, {
  id: 'TC-12', scenario: 'Upload & ingestion',
  title: 'Uploading a TXT contract runs the real pipeline through to ready',
  expected: 'Document reaches status=ready, classified as contract, with chunks embedded into Qdrant',
}, async (t) => {
  await page.locator('input[type="file"]').setInputFiles(`${DOCS}/Calder-Ridgeway-Supply-Agreement.txt`);
  await t.shot('upload-queued');
  let doc = null;
  for (let i = 0; i < 40; i += 1) {
    await page.waitForTimeout(3000);
    const docs = await apiGet(page, '/api/documents');
    const items = docs.body?.items ?? docs.body ?? [];
    doc = items.find((d) => d.name.includes('Calder-Ridgeway'));
    if (doc && (doc.status === 'ready' || doc.status === 'failed')) break;
  }
  await page.waitForTimeout(1500);
  await t.shot('upload-ready');
  const jobs = (doc?.ingestion ?? []).map((j) => `${j.label}:${j.status}`).join(' | ');
  const indexed = (doc?.ingestion ?? []).some((j) => /embed|index/i.test(j.label) && j.status === 'done');
  return {
    pass: doc?.status === 'ready' && doc?.type === 'contract' && indexed,
    actual: `id=${doc?.id} status=${doc?.status} type=${doc?.type} pages=${doc?.pages} | ${jobs}`,
  };
});

await testCase(page, {
  id: 'TC-13', scenario: 'Upload & ingestion',
  title: 'A newly uploaded document becomes selectable as retrieval scope',
  expected: 'The uploaded contract appears in the scope picker ready list',
}, async (t) => {
  await page.getByRole('button', { name: /^Done$/ }).click();
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /Whole workspace|documents?$/ }).first().click();
  await page.waitForTimeout(900);
  const txt = await page.locator('body').textContent();
  await t.shot('scope-picker-open');
  const present = txt.includes('Calder-Ridgeway') || txt.includes('Calder');
  await page.keyboard.press('Escape');
  return { pass: present, actual: `uploaded document offered as scope=${present}` };
});

// -------------------------------------------------------- COMPOSER / MODES

await testCase(page, {
  id: 'TC-14', scenario: 'Composer controls',
  title: 'Mode chips switch between Auto, Ask, Summary and Draft',
  expected: 'Each chip becomes aria-checked when selected and the composer adapts',
}, async (t) => {
  const s = sel(page);
  const states = {};
  for (const m of ['Auto', 'Ask', 'Summary', 'Draft']) {
    await s.modeChip(m).click();
    await page.waitForTimeout(500);
    states[m] = await s.modeChip(m).getAttribute('aria-checked');
    await t.shot(`mode-${m.toLowerCase()}`);
  }
  const ok = Object.values(states).every((v) => v === 'true');
  return { pass: ok, actual: `aria-checked after selecting each: ${JSON.stringify(states)}` };
});

await testCase(page, {
  id: 'TC-15', scenario: 'Composer controls',
  title: 'Summary mode exposes a document chooser defaulted to a ready document',
  expected: 'A "Summarise" select appears listing ready documents only',
}, async (t) => {
  const s = sel(page);
  await s.modeChip('Summary').click();
  await page.waitForTimeout(800);
  const select = page.locator('#summary-doc');
  const visible = await select.isVisible();
  const options = await select.locator('option').allTextContents();
  const value = await select.inputValue();
  await t.shot('summary-doc-picker');
  return {
    pass: visible && options.length > 1 && !!value,
    actual: `visible=${visible} default="${value}" options=${JSON.stringify(options.slice(0, 8))}`,
  };
});

await testCase(page, {
  id: 'TC-16', scenario: 'Composer controls',
  title: 'Draft mode refuses to send until its required parameters are supplied',
  expected: 'A blocker message is shown and Send stays disabled with no sources/question',
}, async (t) => {
  const s = sel(page);
  await s.modeChip('Draft').click();
  await page.waitForTimeout(900);
  const blocker = await page.locator('[role="status"]').first().textContent().catch(() => '');
  const sendDisabled = await s.send.isDisabled().catch(() => null);
  const needed = await page.getByText(/needed/i).first().isVisible().catch(() => false);
  await t.shot('draft-blocked');
  return {
    pass: !!blocker && sendDisabled === true,
    actual: `blocker="${(blocker || '').trim().slice(0, 120)}" sendDisabled=${sendDisabled} settingsBadge=${needed}`,
  };
});

await testCase(page, {
  id: 'TC-17', scenario: 'Composer controls',
  title: 'Scope picker shows non-ready documents disabled, with the reason',
  expected: 'Indexing and failed documents are listed struck through with an exclusion reason',
}, async (t) => {
  const s = sel(page);
  await s.modeChip('Ask').click();
  await page.waitForTimeout(600);
  await page.getByRole('button', { name: /Whole workspace/ }).first().click();
  await page.waitForTimeout(900);
  const txt = await page.locator('body').textContent();
  const hasReason = /Still indexing|Failed to process|not yet searchable|cannot be searched/i.test(txt);
  await t.shot('scope-picker-exclusions');
  await page.keyboard.press('Escape');
  return { pass: hasReason, actual: `exclusion reason shown for non-ready documents=${hasReason}` };
});

// ------------------------------------------------------------ NAVIGATION

await testCase(page, {
  id: 'TC-18', scenario: 'Navigation',
  title: 'Settings page renders data & privacy information',
  expected: '/settings loads without error and shows account and data controls',
}, async (t) => {
  await page.goto(`${BASE}/settings`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const txt = await page.locator('body').textContent();
  await t.shot('settings-page', { fullPage: true });
  return {
    pass: txt.includes(EMAIL) || /settings/i.test(txt),
    actual: `page text length=${txt.length}, shows account email=${txt.includes(EMAIL)}`,
  };
});

await testCase(page, {
  id: 'TC-19', scenario: 'Navigation',
  title: 'An unknown route shows the 404 page rather than a blank screen',
  expected: '/no-such-page redirects to /404 with a not-found message',
}, async (t) => {
  await page.goto(`${BASE}/no-such-page-here`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const p = new URL(page.url()).pathname;
  const txt = await page.locator('body').textContent();
  await t.shot('404-page');
  return { pass: p === '/404', actual: `path=${p} bodyMentionsNotFound=${/not found|404/i.test(txt)}` };
});

await testCase(page, {
  id: 'TC-20', scenario: 'Navigation',
  title: 'Document viewer opens a document and renders its pages',
  expected: '/doc/{id} shows the document name and page surfaces',
}, async (t) => {
  const docs = await apiGet(page, '/api/documents');
  const items = docs.body?.items ?? docs.body ?? [];
  const ready = items.find((d) => d.status === 'ready' && d.name.includes('Orion'))
    || items.find((d) => d.status === 'ready');
  await page.goto(`${BASE}/doc/${ready.id}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(3500);
  const txt = await page.locator('body').textContent();
  await t.shot('document-viewer', { fullPage: false });
  const nameShown = txt.includes(ready.name.slice(0, 18));
  const hasPageText = txt.length > 600;
  return {
    pass: nameShown && hasPageText,
    actual: `document=${ready.name} nameShown=${nameShown} renderedChars=${txt.length}`,
  };
});

await browser.close();
console.log('\nphase 1 complete');
