/**
 * Phase 4 — Scenario 2b: in a new conversation, upload a brand-new document
 * and summarise it through the Summary workflow.
 */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const DOCS = '/private/tmp/claude-501/-Users-sekhar-Desktop-Batch-B7-LegalLens/c3bb957e-e716-4268-a946-86b8fd1e820e/scratchpad/testdocs';
const FILE = `${DOCS}/Thalberg-v-Winslow-Freight.txt`;

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);
const state = { doc: null, convId: null, turn: null };

await testCase(page, {
  id: 'TC-41', scenario: 'S2b · Upload then summarise',
  title: 'A new document can be uploaded from inside a new conversation',
  expected: 'Upload dialog accepts the file without leaving the conversation and the document reaches ready',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: /Upload documents/ }).first().click();
  await page.waitForTimeout(800);
  await t.shot('upload-from-conversation');

  await page.locator('input[type="file"]').setInputFiles(FILE);
  await page.waitForTimeout(2500);
  await t.shot('upload-in-progress');

  for (let i = 0; i < 40; i += 1) {
    await page.waitForTimeout(3000);
    const res = await apiGet(page, '/api/documents');
    const items = Array.isArray(res.body) ? res.body : [];
    state.doc = items.find((d) => d.name.includes('Thalberg'));
    if (state.doc && (state.doc.status === 'ready' || state.doc.status === 'failed')) break;
  }
  await page.waitForTimeout(1200);
  await t.shot('upload-finished-ready');

  const jobs = (state.doc?.ingestion ?? []).map((j) => `${j.label}:${j.status}`).join(' | ');
  const indexed = (state.doc?.ingestion ?? []).some(
    (j) => /embed|index/i.test(j.label) && j.status === 'done',
  );
  return {
    pass: state.doc?.status === 'ready' && indexed,
    actual: `id=${state.doc?.id} status=${state.doc?.status} detectedType=${state.doc?.type} ` +
      `pages=${state.doc?.pages}; pipeline: ${jobs}`,
  };
});

await testCase(page, {
  id: 'TC-42', scenario: 'S2b · Upload then summarise',
  title: 'The document type is detected and drives the summary template',
  expected: 'A judicial opinion is classified as an opinion and gets the opinion template, not the contract one',
}, async (t) => {
  await page.getByRole('button', { name: /^Done$/ }).click();
  await page.waitForTimeout(1200);
  await s.modeChip('Summary').click();
  await page.waitForTimeout(900);

  const select = page.locator('#summary-doc');
  const options = await select.locator('option').allTextContents();
  const label = options.find((o) => o.includes('Thalberg'));
  await select.selectOption({ label });
  await page.waitForTimeout(900);
  const row = (await page.locator('label[for="summary-doc"]').locator('..').textContent())
    .replace(/\s+/g, ' ').trim();
  await t.shot('new-doc-selected-for-summary');

  return {
    pass: state.doc?.type === 'opinion' && /opinion template/i.test(row),
    actual: `classifier=${state.doc?.type}; composer states "${row.slice(0, 110)}"`,
  };
});

await testCase(page, {
  id: 'TC-43', scenario: 'S2b · Upload then summarise',
  title: 'Summary of the freshly uploaded document completes with grounded fields',
  expected: 'Turn completes as kind=summary with opinion-template fields, each cited to the new document',
}, async (t) => {
  await s.prompt.fill('Summarise this opinion.');
  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 30000 });
  state.convId = new URL(page.url()).pathname.split('/c/')[1];
  await page.waitForTimeout(6000);
  await t.shot('new-doc-summary-streaming', { fullPage: true });

  await waitForTurn(page, 1500000);
  await page.waitForTimeout(1500);
  await t.shot('new-doc-summary-complete', { fullPage: true });

  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  state.turn = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const sum = state.turn?.summary;
  const fields = sum?.fields ?? [];
  const cites = state.turn?.citations ?? [];
  const allToNewDoc = cites.length > 0 && cites.every((c) => c.documentId === state.doc.id);
  const filled = fields.filter((f) => f.valueMd !== null);

  return {
    pass: state.turn?.status === 'complete' && state.turn?.kind === 'summary'
      && fields.length >= 5 && allToNewDoc,
    actual: `status=${state.turn?.status} template="${sum?.templateName}" ` +
      `fields=${fields.length} (${filled.length} with a value, ${fields.length - filled.length} absent): ` +
      `${fields.map((f) => f.label).join(' · ')}; citations=${cites.length} ` +
      `allPointAtUploadedDocument=${allToNewDoc}`,
  };
});

await testCase(page, {
  id: 'TC-44', scenario: 'S2b · Upload then summarise',
  title: 'Summary content is factually faithful to the uploaded opinion',
  expected: 'The holding field reflects the actual holding (surcharge authorised / judgment affirmed)',
}, async (t) => {
  const fields = state.turn?.summary?.fields ?? [];
  const joined = fields.map((f) => `${f.label}: ${f.valueMd ?? '(absent)'}`).join('\n');
  const holding = fields.find((f) => /holding|disposition|outcome/i.test(f.label));
  const body = (holding?.valueMd ?? joined).toLowerCase();
  const affirmed = /affirm/i.test(joined);
  const surcharge = /surcharge|fuel|4\(c\)/i.test(joined);
  const noHallucinatedCourt = !/ninth circuit|supreme court of the united states/i.test(joined);
  await t.shot('summary-field-contents', { fullPage: true });
  return {
    pass: affirmed && surcharge && noHallucinatedCourt,
    actual: `mentionsAffirmed=${affirmed} mentionsFuelSurcharge=${surcharge} ` +
      `noWrongCourtInvented=${noHallucinatedCourt}; holdingField="${(holding?.valueMd ?? '').slice(0, 220)}"`,
  };
});

await browser.close();
console.log('\nphase 4 complete');
