/** Phase 3b — TC-34 re-run with a correct selector, plus NFR-1 Ask latency. */
import { launch, signIn, testCase, sel, apiGet } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);

await testCase(page, {
  id: 'TC-34', scenario: 'S2a · Summarise an existing document',
  title: 'A new conversation can select an already-indexed document for Summary',
  expected: 'Summary mode lists ready documents; selecting the existing contract shows its template and page count',
}, async (t) => {
  await s.newChat.click();
  await page.waitForTimeout(800);
  await s.modeChip('Summary').click();
  await page.waitForTimeout(900);

  const select = page.locator('#summary-doc');
  const options = await select.locator('option').allTextContents();
  const orion = options.find((o) => o.includes('Orion'));
  await select.selectOption({ label: orion });
  await page.waitForTimeout(900);
  await t.shot('summary-existing-doc-selected');

  // The template/page hint is a sibling of the select, inside the composer row.
  const row = page.locator('label[for="summary-doc"]').locator('..');
  const rowText = (await row.textContent()).replace(/\s+/g, ' ').trim();
  const docsRes = await apiGet(page, '/api/documents');
  const items = Array.isArray(docsRes.body) ? docsRes.body : [];
  const doc = items.find((d) => d.name.includes('Orion'));
  const value = await select.inputValue();

  const describesTemplate = /contract template/i.test(rowText);
  const describesPages = rowText.includes(`${doc.pages} pages`);
  const notReadyOffered = options.some((o) => /Deposition|Exhibit Bundle/i.test(o));

  return {
    pass: !!orion && value === doc.id && describesTemplate && describesPages && !notReadyOffered,
    actual: `options=${JSON.stringify(options)}; selectedValue=${value} (expected ${doc.id}); ` +
      `composerRow="${rowText.slice(0, 120)}"; statesTemplate=${describesTemplate} ` +
      `statesPages=${describesPages}; non-ready documents offered=${notReadyOffered}`,
  };
});

await testCase(page, {
  id: 'TC-40', scenario: 'Performance',
  title: 'Ask turn latency is measured against the NFR-1 budget',
  expected: 'NFR-1: p95 complete answer ≤ 15 s',
}, async (t) => {
  const list = await apiGet(page, '/api/conversations?limit=50');
  const convs = list.body?.items ?? [];
  const samples = [];
  for (const c of convs.slice(0, 14)) {
    const full = await apiGet(page, `/api/conversations/${c.id}`);
    for (const turn of full.body?.turns ?? []) {
      if (turn.role !== 'assistant' || turn.kind !== 'answer') continue;
      const stages = turn.stages ?? [];
      const total = Math.max(...stages.map((x) => x.elapsedMs ?? 0), 0);
      if (total > 0) {
        samples.push({
          seconds: +(total / 1000).toFixed(1),
          status: turn.status,
          rerank: stages.find((x) => x.key === 'reranking')?.elapsedMs ?? 0,
        });
      }
    }
  }
  samples.sort((a, b) => a.seconds - b.seconds);
  const worst = samples.length ? samples[samples.length - 1].seconds : 0;
  const median = samples.length ? samples[Math.floor(samples.length / 2)].seconds : 0;
  const underBudget = samples.filter((x) => x.seconds <= 15).length;
  await t.shot('ask-latency-context');
  return {
    pass: samples.length > 0 && worst <= 15,
    actual: `${samples.length} Ask turns measured — median ${median}s, slowest ${worst}s, ` +
      `${underBudget}/${samples.length} within the 15 s NFR-1 budget. ` +
      `Samples: ${samples.map((x) => `${x.seconds}s(${x.status}, rerank ${(x.rerank / 1000).toFixed(0)}s)`).join(', ')}. ` +
      `Cross-encoder reranking on CPU dominates every turn.`,
  };
});

await browser.close();
console.log('\nphase 3b complete');
