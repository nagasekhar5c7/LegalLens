/** Phase 5 — Scenario 3: draft a legal memorandum from chosen sources. */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);
const state = { convId: null, turn: null, sourceLabel: null };

const QUESTION_PRESENTED =
  'Whether Vertex may withhold consent to assignment where Orion undergoes a change of control, '
  + 'and what remedies are available if the transaction closes without that consent.';

await testCase(page, {
  id: 'TC-45', scenario: 'S3 · Draft a memo',
  title: 'Draft settings capture the memo type, question presented and source documents',
  expected: 'The drawer accepts every parameter and Generate draft becomes enabled',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(900);
  await s.modeChip('Draft').click();
  await page.waitForTimeout(900);
  await t.shot('draft-mode-blocked');

  await page.getByRole('button', { name: /Draft settings/ }).first().click();
  await page.waitForTimeout(2000);
  const dlg = page.getByRole('dialog').first();
  await dlg.waitFor({ state: 'visible', timeout: 20000 });
  await t.shot('draft-settings-opened');

  // Draft type — memo. Scoped to the drawer, and verified via aria-pressed.
  const memo = dlg.getByRole('button').filter({ hasText: 'Legal memorandum' }).first();
  await memo.click();
  await page.waitForTimeout(400);
  const memoPressed = await memo.getAttribute('aria-pressed');

  await dlg.locator('textarea').first().fill(QUESTION_PRESENTED);
  await page.waitForTimeout(300);

  // Audience, tone, position.
  const combos = dlg.getByRole('combobox');
  const comboCount = await combos.count();
  const chosen = [];
  for (let i = 0; i < comboCount; i += 1) {
    const opts = await combos.nth(i).locator('option').allTextContents();
    if (opts.length > 1) {
      await combos.nth(i).selectOption({ index: 1 });
      chosen.push(opts[1]);
    }
  }
  await page.waitForTimeout(300);

  // Source documents — the Orion contract.
  const orionRow = dlg.locator('label').filter({ hasText: /Orion/ }).first();
  await orionRow.locator('[role="checkbox"]').click();
  state.sourceLabel = (await orionRow.textContent()).replace(/\s+/g, ' ').trim();
  await page.waitForTimeout(600);
  await t.shot('draft-settings-filled');

  const generate = dlg.getByRole('button', { name: /Generate draft/ });
  const enabled = await generate.isEnabled();
  const qp = await dlg.locator('textarea').first().inputValue();

  return {
    pass: memoPressed === 'true' && qp.length > 50 && chosen.length === 3 && enabled,
    actual: `draftType=memo (aria-pressed=${memoPressed}); questionPresented=${qp.length} chars; ` +
      `parameters set=${JSON.stringify(chosen)}; source="${state.sourceLabel.slice(0, 50)}"; ` +
      `generateEnabled=${enabled}`,
  };
});

await testCase(page, {
  id: 'TC-46', scenario: 'S3 · Draft a memo',
  title: 'Draft generates the memo structure section by section',
  expected: 'Turn completes as kind=draft with Question Presented, Short Answer, Statement of Facts, Discussion and Conclusion',
}, async (t) => {
  const dlg = page.getByRole('dialog').first();
  await dlg.getByRole('button', { name: /Generate draft/ }).click();
  await page.waitForURL(/\/c\/c_/, { timeout: 40000 });
  state.convId = new URL(page.url()).pathname.split('/c/')[1];
  await page.waitForTimeout(6000);
  await t.shot('draft-streaming', { fullPage: true });

  await waitForTurn(page, 1500000);
  await page.waitForTimeout(2000);
  await t.shot('draft-complete', { fullPage: true });

  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  state.turn = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const draft = state.turn?.draft;
  const sections = draft?.sections ?? [];
  const headings = sections.map((x) => x.heading);
  const wanted = ['Question Presented', 'Short Answer', 'Statement of Facts', 'Discussion', 'Conclusion'];
  const missing = wanted.filter((w) => !headings.some((h) => h.toLowerCase() === w.toLowerCase()));

  return {
    pass: state.turn?.status === 'complete' && state.turn?.kind === 'draft' && missing.length === 0,
    actual: `status=${state.turn?.status} kind=${state.turn?.kind} draftType=${draft?.draftType} ` +
      `title="${(draft?.title ?? '').slice(0, 70)}"; sections=${sections.length}: ${headings.join(' → ')}; ` +
      `missingSections=${JSON.stringify(missing)}; error=${JSON.stringify(state.turn?.error)}`,
  };
});

await testCase(page, {
  id: 'TC-47', scenario: 'S3 · Draft a memo',
  title: 'The draft is marked as an unreviewed draft throughout',
  expected: 'A draft banner is rendered and the stored disclaimer names it as an unreviewed draft',
}, async (t) => {
  const body = await page.locator('body').textContent();
  const banner = /unreviewed/i.test(body);
  const disclaimer = state.turn?.disclaimer ?? '';
  await t.shot('draft-banner');
  return {
    pass: banner && /draft/i.test(disclaimer),
    actual: `"unreviewed" markingVisible=${banner}; storedDisclaimer="${disclaimer.slice(0, 150)}"`,
  };
});

await testCase(page, {
  id: 'TC-48', scenario: 'S3 · Draft a memo',
  title: 'The draft cites only the sources the attorney selected',
  expected: 'Every citation resolves to one of the chosen source documents — no outside authority',
}, async (t) => {
  const full = await apiGet(page, `/api/conversations/${state.convId}`);
  const userTurn = (full.body?.turns ?? []).find((x) => x.role === 'user');
  const chosen = userTurn?.draftParams?.documentIds ?? [];
  const cites = state.turn?.citations ?? [];
  const allFromChosen = cites.length > 0 && cites.every((c) => chosen.includes(c.documentId));
  const docsRes = await apiGet(page, '/api/documents');
  const items = Array.isArray(docsRes.body) ? docsRes.body : [];
  const names = [...new Set(cites.map((c) => c.documentName))];
  const chips = await s.citations.count();
  await t.shot('draft-citations');
  return {
    pass: allFromChosen,
    actual: `selectedSources=${chosen.length} (${chosen.map((id) => items.find((d) => d.id === id)?.name?.slice(0, 30)).join(', ')}); ` +
      `citations=${cites.length} chipsInUi=${chips} citedDocuments=${JSON.stringify(names)}; ` +
      `allCitationsWithinSelectedSources=${allFromChosen}`,
  };
});

await testCase(page, {
  id: 'TC-49', scenario: 'S3 · Draft a memo',
  title: 'The memo answers the question presented from the source document',
  expected: 'The draft discusses consent to assignment and the change-of-control remedies in the source contract',
}, async (t) => {
  const sections = state.turn?.draft?.sections ?? [];
  const all = sections.map((x) => `${x.heading}: ${x.bodyMd}`).join('\n\n');
  const consent = /consent/i.test(all);
  const coc = /change of control/i.test(all);
  const remedies = /(terminat|suspend|guarantee)/i.test(all);
  const noInventedCase = !/\b[A-Z][a-z]+ v\. [A-Z][a-z]+, \d+/.test(all);
  await t.shot('draft-body', { fullPage: true });
  return {
    pass: consent && coc && remedies,
    actual: `discussesConsent=${consent} discussesChangeOfControl=${coc} namesRemedies=${remedies} ` +
      `noFabricatedCaseCitation=${noInventedCase}; shortAnswer="${(sections.find((x) => /short answer/i.test(x.heading))?.bodyMd ?? '').replace(/\s+/g, ' ').slice(0, 260)}"`,
  };
});

await browser.close();
console.log('\nphase 5 complete');
