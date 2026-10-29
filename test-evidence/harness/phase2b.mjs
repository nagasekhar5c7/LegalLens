/** Phase 2b — citation resolution, refusal behaviour, and retrieval recall. */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);

const ORION = 'Orion-Vertex-Services-Agreement.txt';

/** Ask one question in a fresh conversation and return the stored turn. */
async function ask(text, { scopeDoc } = {}) {
  await s.newChat.click();
  await page.waitForTimeout(700);
  await s.modeChip('Ask').click();
  await page.waitForTimeout(300);
  if (scopeDoc) {
    await page.getByRole('button', { name: /Whole workspace/ }).first().click();
    await page.waitForTimeout(700);
    await page.getByRole('checkbox').first().click().catch(() => {});
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  }
  await s.prompt.fill(text);
  await s.send.click();
  await page.waitForURL(/\/c\/c_/, { timeout: 30000 });
  const convId = new URL(page.url()).pathname.split('/c/')[1];
  await waitForTurn(page, 480000);
  await page.waitForTimeout(1200);
  const stored = await apiGet(page, `/api/conversations/${convId}`);
  const turn = (stored.body?.turns ?? []).filter((x) => x.role === 'assistant').pop();
  return { convId, turn };
}

// ------------------------------------------------- TC-23  citations resolve

await testCase(page, {
  id: 'TC-23', scenario: 'S1 · Citations',
  title: 'Every citation resolves to a document, page and span the user owns',
  expected: 'Chips render and each stored citation names an existing document with an in-range page and a span id',
}, async (t) => {
  const { turn } = await ask(
    'What happens if a change of control occurs without the consent required by the agreement?',
  );
  const chips = await s.citations.count();
  const cites = turn?.citations ?? [];
  const docsRes = await apiGet(page, '/api/documents');
  const items = Array.isArray(docsRes.body) ? docsRes.body : (docsRes.body?.items ?? []);
  const byId = new Map(items.map((d) => [d.id, d]));

  const checks = cites.map((c) => {
    const doc = byId.get(c.documentId);
    return {
      n: c.n, doc: c.documentName?.slice(0, 28), page: c.page, section: c.section,
      documentExists: !!doc,
      pageInRange: !!doc && c.page >= 1 && c.page <= doc.pages,
      hasSpan: !!c.spanId,
      excerptPresent: (turn?.excerpts ?? []).some((e) => e.spanId === c.spanId),
    };
  });
  const allResolve = cites.length > 0 && checks.every(
    (c) => c.documentExists && c.pageInRange && c.hasSpan && c.excerptPresent,
  );
  await t.shot('citations-resolve', { fullPage: true });
  return {
    pass: chips > 0 && allResolve,
    actual: `chipsInUi=${chips} storedCitations=${cites.length}; ` +
      checks.map((c) => `[${c.n}]→${c.doc} p${c.page} ${c.section ?? ''} ` +
        `(docExists=${c.documentExists} pageInRange=${c.pageInRange} span=${c.hasSpan} excerpt=${c.excerptPresent})`).join('; '),
  };
});

// ------------------------------------------- TC-32  refusal is a success state

await testCase(page, {
  id: 'TC-32', scenario: 'S1 · Guardrails',
  title: 'A question the corpus does not cover is declined, not answered',
  expected: 'Refusal card shown with the queries it ran, the relevance it got, and actionable suggestions — no invented answer',
}, async (t) => {
  const { turn } = await ask('What does this agreement say about force majeure and acts of God?');
  const refusal = turn?.refusal;
  const visible = await s.refusal.isVisible().catch(() => false);
  await t.shot('refusal-card', { fullPage: true });

  // The refusal must be auditable: expand "what was searched for".
  const expander = page.locator('button', { hasText: /What was searched for/i }).first();
  if (await expander.isVisible().catch(() => false)) {
    await expander.click();
    await page.waitForTimeout(600);
    await t.shot('refusal-queries-expanded');
  }
  const noProse = (turn?.text ?? '').trim().length === 0;
  return {
    pass: turn?.status === 'refused' && !!refusal && visible && noProse
      && (refusal.lookedFor ?? []).length > 0 && (refusal.suggestions ?? []).length > 0,
    actual: `status=${turn?.status} kind=${refusal?.kind} cardVisible=${visible} ` +
      `noInventedProse=${noProse} queriesShown=${(refusal?.lookedFor ?? []).length} ` +
      `suggestions=${(refusal?.suggestions ?? []).map((x) => x.kind).join(',')} ` +
      `best=${refusal?.bestRelevance?.toFixed(3)} floor=${refusal?.threshold}`,
  };
});

// ----------------------------------- TC-33  recall sensitivity to paraphrase

await testCase(page, {
  id: 'TC-33', scenario: 'S1 · Retrieval quality',
  title: 'A paraphrased question about indexed text is declined while a near-verbatim one succeeds',
  expected: 'Both phrasings should find § 6.1; this records whether paraphrase recall holds',
}, async (t) => {
  const paraphrase = await ask('What is the liability cap and what carve-outs apply to it?');
  await t.shot('paraphrase-declined', { fullPage: true });
  const verbatim = await ask("What is the cap on each party's total aggregate liability?");
  await t.shot('verbatim-answered', { fullPage: true });

  const pBest = paraphrase.turn?.refusal?.bestRelevance;
  const vCites = (verbatim.turn?.citations ?? []).map((c) => c.section).join(',');
  const paraphraseWorked = (paraphrase.turn?.citations ?? []).length > 0;
  const verbatimWorked = (verbatim.turn?.citations ?? []).length > 0;

  return {
    pass: paraphraseWorked && verbatimWorked,
    actual: `paraphrase("liability cap … carve-outs"): status=${paraphrase.turn?.status} ` +
      `bestRelevance=${pBest?.toFixed(4)} vs floor 0.25 → ${paraphraseWorked ? 'answered' : 'DECLINED'}; ` +
      `near-verbatim("cap on … total aggregate liability"): status=${verbatim.turn?.status} ` +
      `cited=${vCites || 'none'} → ${verbatimWorked ? 'answered' : 'declined'}. ` +
      `Same clause (§ 6.1) in the same document, ${ORION}.`,
  };
});

await browser.close();
console.log('\nphase 2b complete');
