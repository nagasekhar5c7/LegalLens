/**
 * Where the API lives.
 *
 * An empty base means *same origin*, which is what the Vite dev proxy in
 * `vite.config.ts` provides: the browser talks to `localhost:5173/api` and Vite
 * forwards to the backend. That is deliberate rather than lazy — the session
 * cookie is `SameSite=Lax`, and while it would ride along from
 * `localhost:5173` to `localhost:8000` (a port is not part of a "site"), it
 * would *not* from `127.0.0.1:5173`. Same-origin makes that whole class of
 * "login succeeded but everything 401s" bug impossible locally.
 *
 * `VITE_API_BASE_URL` overrides it with an absolute origin — which is what
 * `docker-compose.yml` already sets, so the cross-origin path stays exercised.
 */
const RAW = import.meta.env.VITE_API_BASE_URL?.trim() ?? ''

/** No trailing slash, so `${API_BASE}/api/config` is always well formed. */
export const API_BASE = RAW.replace(/\/+$/, '')

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}
