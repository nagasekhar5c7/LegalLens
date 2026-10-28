/**
 * Phase 3 — Scenario 2a: in a new conversation, select an existing document
 * and ask Legal Lens to summarise it through the Summary workflow.
 */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);
const state = { convId: null, turn: null, doc: null };

const SUMMARY_TIMEOUT = 1500000; // 25 min — 9 field searches on CPU

await testCase(page, {
  id: 'TC-34', scenario: 'S2a · Summarise an existing document',
  title: 'A new conversation can select an already-indexed document for Summary',
  expected: 'Summary mode lists the existing contract and selecting it shows its template and page count',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(800);
  await s.modeChip('Summary').click();
  await page.waitForTimeout(900);

  const select = page.locator('#summary-doc');
  const options = await select.locator('option').allTextContents();
  const orion = options.find((o) => o.includes('Orion'));
  await select.selectOption({ label: orion });
  await page.waitForTimeout(800);
  await t.shot('summary-existing-doc-selected');

  const bar = await page.locator('#summary-doc').locator('..').textContent();
  const docsRes = await apiGet(page, '/api/documents');
  const items = Array.isArray(docsRes.body) ? docsRes.body : [];
  state.doc = items.find((d) => d.name.includes('Orion'));

  return {
    pass: !!orion && /contract template/i.test(bar),
    expectedNote: '',
    actual: `selected="${orion}"; composer reports "${bar.replace(/\s+/g, ' ').trim().slice(0, 90)}"; ` +
      `document=${state.doc?.id} pages=${state.doc?.pages} type=${state.doc?.type}`,
  };
});

await testCase(page, {
  id: 'TC-35', scenario: 'S2a · Summarise an existing document',
  title: 'Summary runs the per-field workflow and completes against the contract template',
  expected: 'Turn completes with kind=summary and one field per contract template entry',
}, async (t) => {
  await s.prompt.fill('Summarise this agreement.');
  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 30000 });
  state.convId = new URL(page.url()).pathname.split('/c/')[1];
  await page.waitForTimeout(6000);
  await t.shot('summary-streaming', { fullPage: true });

  await waitForTurn(page, SUMMARY_TIMEOUT);
  await page.waitForTimeout(1500);
  await t.shot('summary-complete', { fullPage: true });

  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  state.turn = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const sum = state.turn?.summary;
  const fields = sum?.fields ?? [];
  const labels = fields.map((f) => f.label);

  return {
    pass: state.turn?.status === 'complete' && state.turn?.kind === 'summary' && fields.length >= 7,
    actual: `status=${state.turn?.status} kind=${state.turn?.kind} template="${sum?.templateName}" ` +
      `document="${sum?.documentName}" fields=${fields.length}: ${labels.join(' · ')}`,
  };
});

await testCase(page, {
  id: 'TC-36', scenario: 'S2a · Summarise an existing document',
  title: 'A term absent from the document is reported as absent, not inferred',
  expected: 'Any field with no support renders "Not addressed in this document" with valueMd null',
}, async (t) => {
  const fields = state.turn?.summary?.fields ?? [];
  const absent = fields.filter((f) => f.valueMd === null);
  const present = fields.filter((f) => f.valueMd !== null);
  const body = await page.locator('body').textContent();
  const shown = /Not addressed in this document/i.test(body);
  await t.shot('summary-absent-field');
  return {
    pass: absent.length === 0 ? present.length > 0 : shown,
    actual: `fieldsPresent=${present.length} fieldsAbsent=${absent.length}` +
      (absent.length ? ` (${absent.map((f) => f.label).join(', ')}) uiShowsNotAddressed=${shown}` : ' — every template field had support in this document'),
  };
});

await testCase(page, {
  id: 'TC-37', scenario: 'S2a · Summarise an existing document',
  title: 'Summary fields carry citations that resolve to the summarised document',
  expected: 'Each cited field points at a span in the one document being summarised',
}, async (t) => {
  const cites = state.turn?.citations ?? [];
  const fields = state.turn?.summary?.fields ?? [];
  const cited = fields.filter((f) => (f.citations ?? []).length > 0);
  const allToThisDoc = cites.length > 0 && cites.every((c) => c.documentId === state.doc.id);
  const numbersValid = cited.every((f) => (f.citations ?? []).every((n) => cites.some((c) => c.n === n)));
  const chips = await s.citations.count();
  await t.shot('summary-citations');
  return {
    pass: cites.length > 0 && allToThisDoc && numbersValid && cited.length > 0,
    actual: `citations=${cites.length} chipsInUi=${chips} fieldsWithCitations=${cited.length}/${fields.length} ` +
      `allPointAtSummarisedDocument=${allToThisDoc} everyFieldNumberResolves=${numbersValid}; ` +
      `sections=${cites.map((c) => `[${c.n}]${c.section ?? ''}p${c.page}`).join(' ')}`,
  };
});

await testCase(page, {
  id: 'TC-38', scenario: 'S2a · Summarise an existing document',
  title: 'Coverage is reported honestly rather than claiming the whole document was read',
  expected: 'A coverage note states pages covered out of total pages and the number of passes',
}, async (t) => {
  const cov = state.turn?.coverage;
  const body = await page.locator('body').textContent();
  const shown = cov ? body.includes(`${cov.pagesCovered} of ${cov.totalPages} pages`) : false;
  await t.shot('summary-coverage-note');
  return {
    pass: !!cov && cov.totalPages > 0 && shown,
    actual: `coverage=${cov?.pagesCovered}/${cov?.totalPages} pages · ${cov?.passes} passes; ` +
      `note="${(cov?.note ?? '').slice(0, 90)}"; shownInUi=${shown}`,
  };
});

await testCase(page, {
  id: 'TC-39', scenario: 'S2a · Summarise an existing document',
  title: 'Summary turn latency is measured against the NFR-3 budget',
  expected: 'A summary completes inside the stated three-minute budget',
}, async (t) => {
  const stages = state.turn?.stages ?? [];
  const last = stages[stages.length - 1];
  const totalMs = Math.max(...stages.map((x) => x.elapsedMs ?? 0), 0);
  const usage = state.turn?.usage;
  await t.shot('summary-stage-timings');
  return {
    pass: totalMs > 0 && totalMs <= 180000,
    actual: `measured wall time to last stage (${last?.key}) = ${(totalMs / 1000).toFixed(1)}s ` +
      `against the 180s NFR-3 budget; cost=$${usage?.usd ?? '?'} ` +
      `(tokens in/out ${usage?.inputTokens}/${usage?.outputTokens}); ` +
      `stages=${stages.map((x) => `${x.key}:${((x.elapsedMs ?? 0) / 1000).toFixed(0)}s`).join(' ')}`,
  };
});

await browser.close();
console.log('\nphase 3 complete');
