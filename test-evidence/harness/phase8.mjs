/** Phase 8 — regression evidence for the TC-59 segfault fix. */
import { launch, signIn, testCase, sel, apiGet, waitForTurn } from './harness.mjs';

const { browser, page } = await launch();
await signIn(page);
const s = sel(page);

await testCase(page, {
  id: 'TC-61', scenario: 'Stability',
  title: 'Overlapping turns no longer crash the backend (regression for TC-59)',
  expected: 'Several turns dispatched at once all complete, and the process stays up',
}, async (t) => {
  const docsRes = await apiGet(page, '/api/documents');
  const items = Array.isArray(docsRes.body) ? docsRes.body : [];
  const orion = items.find((d) => d.name.includes('Orion'));
  const thal = items.find((d) => d.name.includes('Thalberg'));

  const pidBefore = await apiGet(page, '/readyz');

  // Fire four turns from the page at once — two summaries and two asks, which
  // is the embed-versus-rerank race that used to segfault the process.
  const outcome = await page.evaluate(async ({ orionId, thalId }) => {
    async function conv(title) {
      const r = await fetch('/api/conversations', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      return (await r.json()).id;
    }
    async function drain(url, body) {
      const r = await fetch(url, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(body),
      });
      const text = await r.text();
      return {
        ok: r.ok,
        done: text.includes('"type":"done"'),
        fields: (text.match(/"type":"summary_field"/g) || []).length,
        citations: (text.match(/"type":"citation"/g) || []).length,
        error: (text.match(/"type":"error"/g) || []).length,
      };
    }
    const [c1, c2, c3, c4] = await Promise.all([
      conv('concurrent A'), conv('concurrent B'), conv('concurrent C'), conv('concurrent D'),
    ]);
    return Promise.all([
      drain('/api/summarize', { conversationId: c1, text: 'Summarise this.', scope: { kind: 'document', documentIds: [thalId] } }),
      drain('/api/summarize', { conversationId: c2, text: 'Summarise this.', scope: { kind: 'document', documentIds: [orionId] } }),
      drain(`/api/conversations/${c3}/messages`, { text: 'What happens on a change of control without consent?', mode: 'ask', scope: { kind: 'document', documentIds: [orionId] } }),
      drain(`/api/conversations/${c4}/messages`, { text: 'What is the cap on each party’s total aggregate liability?', mode: 'ask', scope: { kind: 'document', documentIds: [orionId] } }),
    ]);
  }, { orionId: orion.id, thalId: thal.id });

  const after = await apiGet(page, '/readyz');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  await t.shot('workspace-healthy-after-concurrent-turns', { fullPage: true });

  const allDone = outcome.every((o) => o.ok && o.done && o.error === 0);
  const serverUp = after.status === 200;
  return {
    pass: allDone && serverUp,
    actual: `4 turns dispatched simultaneously (2 Summary + 2 Ask on shared bge-m3/reranker): ` +
      outcome.map((o, i) => `#${i + 1} ok=${o.ok} done=${o.done} fields=${o.fields} cites=${o.citations} errors=${o.error}`).join('; ') +
      `; backend alive afterwards: /readyz=${after.status} (before=${pidBefore.status}). ` +
      `Before the fix, three SEQUENTIAL summaries segfaulted the process.`,
  };
});

await browser.close();
console.log('\nphase 8 complete');
