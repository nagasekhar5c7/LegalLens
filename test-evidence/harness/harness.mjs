/**
 * Legal Lens end-to-end functional test harness.
 *
 * Drives the real UI at http://localhost:5173 against the real backend at
 * 127.0.0.1:8000 with LL_AI_ENGINE=auto resolving to orchestrated/m4 — so every
 * answer, summary and draft below is a live Sonnet 5 generation over bge-m3
 * retrieval, not a fixture.
 *
 * Results stream to results.jsonl as each case finishes, so a suite that dies
 * mid-run still reports what it proved.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

export const BASE = 'http://localhost:5173';
export const API = 'http://127.0.0.1:8000';
export const EMAIL = 'dana@example.com';
export const PASSWORD = 'counsel-demo-2026';

export const EVIDENCE = '/Users/sekhar/Desktop/Batch B7/LegalLens/test-evidence';
export const SHOTS = path.join(EVIDENCE, 'screenshots');
export const RESULTS = path.join(EVIDENCE, 'results.jsonl');

fs.mkdirSync(SHOTS, { recursive: true });

let shotSeq = 0;

export function record(result) {
  fs.appendFileSync(RESULTS, JSON.stringify(result) + '\n');
  const mark = result.status === 'PASS' ? '✓' : result.status === 'FAIL' ? '✗' : '~';
  console.log(`${mark} ${result.id}  ${result.status}  ${result.title}`);
  if (result.status !== 'PASS') console.log(`     ${result.actual}`);
}

/** A screenshot named so the Excel can find it and a human can read it. */
export async function shot(page, id, slug, opts = {}) {
  shotSeq += 1;
  const name = `${id}-${String(shotSeq).padStart(2, '0')}-${slug}.png`;
  const file = path.join(SHOTS, name);
  try {
    await page.screenshot({ path: file, fullPage: !!opts.fullPage, ...opts });
    return name;
  } catch (e) {
    console.log(`   (screenshot failed for ${name}: ${e.message})`);
    return null;
  }
}

/**
 * One test case. `fn` returns { pass, actual, shots } or throws.
 * A throw is a FAIL with the stack, never a suite abort.
 */
export async function testCase(page, { id, title, scenario, expected }, fn) {
  const started = Date.now();
  const shots = [];
  const ctx = {
    shot: async (slug, opts) => {
      const n = await shot(page, id, slug, opts);
      if (n) shots.push(n);
      return n;
    },
  };
  let status = 'FAIL';
  let actual = '';
  try {
    const out = await fn(ctx);
    status = out && out.pass ? 'PASS' : 'FAIL';
    actual = (out && out.actual) || '';
  } catch (e) {
    status = 'FAIL';
    actual = `threw: ${e.message}`;
    await ctx.shot('error-state');
  }
  record({
    id,
    scenario,
    title,
    expected,
    actual,
    status,
    seconds: +((Date.now() - started) / 1000).toFixed(1),
    shots,
    at: new Date().toISOString(),
  });
  return status === 'PASS';
}

export async function launch() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') console.log(`   [browser error] ${m.text().slice(0, 200)}`);
  });
  page.setDefaultTimeout(20000);
  return { browser, context, page };
}

export async function signIn(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.locator('input[type="email"]').fill(EMAIL);
  await page.locator('input[type="password"]').fill(PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 });
  // RequireAuth restores the route the visitor was bounced from, which may not
  // be the chat workspace. Land somewhere with a composer deterministically.
  if (new URL(page.url()).pathname !== '/') {
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  }
  await page.waitForSelector('#prompt', { timeout: 20000 });
}

/** Wait for a streaming turn to settle: the Stop button goes away. */
export async function waitForTurn(page, timeoutMs) {
  const stop = page.getByRole('button', { name: /^Stop$/ });
  // Stop appears quickly once the request is in flight.
  try {
    await stop.waitFor({ state: 'visible', timeout: 15000 });
  } catch {
    /* a turn that refused before streaming may never show Stop */
  }
  await stop.waitFor({ state: 'hidden', timeout: timeoutMs });
  // Let the final patch (usage/done/disclaimer) paint.
  await page.waitForTimeout(1200);
}

export function sel(page) {
  return {
    prompt: page.locator('#prompt'),
    send: page.getByRole('button', { name: 'Send' }),
    newChat: page.getByRole('button', { name: /New chat/ }),
    modeChip: (name) => page.getByRole('radio', { name: new RegExp(`^${name}$`, 'i') }),
    assistant: page.locator('article[aria-label="Legal Lens response"]'),
    citations: page.locator('[data-citation]'),
    evidencePanel: page.locator('aside[aria-label="Evidence — retrieved passages"]'),
    refusal: page.locator('section[aria-label="Not answerable from your documents"]'),
    convLinks: page.locator('nav[aria-label="Conversations"] a'),
  };
}

/** Server-side truth, so a UI assertion can be checked against the record. */
export async function apiGet(page, pathname) {
  return page.evaluate(async (p) => {
    const r = await fetch(p, { credentials: 'include' });
    return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
  }, pathname);
}
