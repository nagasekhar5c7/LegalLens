/** Phase 7 — re-run the uploaded-document summary on a freshly restarted server. */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);
const state = { doc: null, convId: null, turn: null };

const docsRes = await apiGet(page, '/api/documents');
const items = Array.isArray(docsRes.body) ? docsRes.body : [];
state.doc = items.find((d) => d.name.includes('Thalberg'));

await testCase(page, {
  id: 'TC-43', scenario: 'S2b · Upload then summarise',
  title: 'Summary of the freshly uploaded document completes with grounded fields',
  expected: 'Turn completes as kind=summary with every opinion-template field emitted, each cited to the new document',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(900);
  await s.modeChip('Summary').click();
  await page.waitForTimeout(900);
  const select = page.locator('#summary-doc');
  const options = await select.locator('option').allTextContents();
  await select.selectOption({ label: options.find((o) => o.includes('Thalberg')) });
  await page.waitForTimeout(800);

  await s.prompt.fill('Summarise this opinion.');
  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 40000 });
  state.convId = new URL(page.url()).pathname.split('/c/')[1];
  await page.waitForTimeout(6000);
  await t.shot('new-doc-summary-streaming', { fullPage: true });

  await waitForTurn(page, 1500000);
  await page.waitForTimeout(2000);
  await t.shot('new-doc-summary-complete', { fullPage: true });

  const stored = await apiGet(page, `/api/conversations/${state.convId}`);
  state.turn = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  const sum = state.turn?.summary;
  const fields = sum?.fields ?? [];
  const cites = state.turn?.citations ?? [];
  const expected = state.turn?.expectedFieldCount ?? 7;
  const allToNewDoc = cites.length > 0 && cites.every((c) => c.documentId === state.doc.id);
  const filled = fields.filter((f) => f.valueMd !== null);

  return {
    pass: state.turn?.status === 'complete' && state.turn?.kind === 'summary'
      && fields.length >= expected && allToNewDoc,
    actual: `status=${state.turn?.status} template="${sum?.templateName}" ` +
      `fieldsEmitted=${fields.length} of ${expected} expected ` +
      `(${filled.length} with a value, ${fields.length - filled.length} absent): ` +
      `${fields.map((f) => f.label).join(' · ')}; citations=${cites.length} ` +
      `allPointAtUploadedDocument=${allToNewDoc}; error=${JSON.stringify(state.turn?.error)}`,
  };
});

await testCase(page, {
  id: 'TC-44', scenario: 'S2b · Upload then summarise',
  title: 'Summary content is factually faithful to the uploaded opinion',
  expected: 'The holding and disposition fields reflect the real outcome — surcharge authorised, judgment affirmed',
}, async (t) => {
  const fields = state.turn?.summary?.fields ?? [];
  const joined = fields.map((f) => `${f.label}: ${f.valueMd ?? '(absent)'}`).join('\n');
  const holding = fields.find((f) => /holding|disposition|outcome/i.test(f.label));
  const affirmed = /affirm/i.test(joined);
  const surcharge = /surcharge|fuel|4\(c\)/i.test(joined);
  const noWrongCourt = !/ninth circuit|supreme court of the united states/i.test(joined);
  await t.shot('summary-field-contents', { fullPage: true });
  return {
    pass: affirmed && surcharge && noWrongCourt,
    actual: `mentionsAffirmed=${affirmed} mentionsFuelSurcharge=${surcharge} ` +
      `noWrongCourtInvented=${noWrongCourt}; fieldsAvailable=${fields.length}; ` +
      `holdingField="${(holding?.valueMd ?? '(not emitted)').replace(/\s+/g, ' ').slice(0, 240)}"`,
  };
});

await browser.close();
console.log('\nphase 7 complete');
