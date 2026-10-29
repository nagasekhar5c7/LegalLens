import type { ApiError, ApiErrorKind } from './types'
import { apiUrl } from './env'

/**
 * The fetch wrapper every call goes through.
 *
 * FR-28 made structural: the backend's error body *is* an `ApiError` — the same
 * object the `{type:'error'}` stream event carries — so one parser serves both
 * and every failure that reaches a component already names a next action. A
 * thrown `ApiErrorResponse` is the only error shape the stores need to know.
 */
export class ApiErrorResponse extends Error {
  readonly error: ApiError
  readonly status: number

  constructor(error: ApiError, status: number) {
    super(error.message)
    this.name = 'ApiErrorResponse'
    this.error = error
    this.status = status
  }
}

/** Last-resort wording when a failure never reached the application at all. */
const NETWORK_ERROR: ApiError = {
  kind: 'network',
  message: 'The server could not be reached.',
  action: 'Check your connection and retry.',
}

/**
 * Anything thrown anywhere, as an `ApiError`. An `AbortError` is a user
 * decision rather than a failure, so callers check `isAbort` before calling
 * this.
 */
export function toApiError(thrown: unknown): ApiError {
  if (thrown instanceof ApiErrorResponse) return thrown.error
  return NETWORK_ERROR
}

export function isAbort(thrown: unknown): boolean {
  return thrown instanceof DOMException && thrown.name === 'AbortError'
}

/* --------------------------------------------------------- 401 handling */

/**
 * Set by `useSession`, called on any 401.
 *
 * A callback rather than a direct import: `http` is the bottom of the stack and
 * the session store sits two layers above it, so importing it here would be a
 * cycle. One expired session should flip the app to signed-out once, not raise
 * seven toasts that each say "network error".
 */
let onUnauthorized: ((error: ApiError) => void) | null = null

export function setUnauthorizedHandler(handler: (error: ApiError) => void): void {
  onUnauthorized = handler
}

/* ------------------------------------------------------------- requests */

export type QueryValue = string | number | boolean | undefined | null

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  /** Serialised as JSON. Mutually exclusive with `form`. */
  body?: unknown
  /** Multipart. The browser sets the boundary, so we must not set the header. */
  form?: FormData
  query?: Record<string, QueryValue>
  signal?: AbortSignal
  /** Dev-only scenario forcing (`X-LegalLens-Scenario`). Omitted for `auto`. */
  scenario?: string | null
  /** Replay protection on turn creation — one fresh key per send. */
  idempotencyKey?: string
  /**
   * Suppresses the global 401 handler. Exactly one caller wants this: the login
   * endpoint, where a 401 means "wrong password", not "your session lapsed".
   */
  allowUnauthorized?: boolean
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = apiUrl(path)
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export function buildHeaders(opts: RequestOptions): Headers {
  const headers = new Headers({ Accept: 'application/json' })
  // Never set Content-Type for FormData: the boundary comes from the browser.
  if (opts.body !== undefined && !opts.form) headers.set('Content-Type', 'application/json')
  if (opts.scenario && opts.scenario !== 'auto') headers.set('X-LegalLens-Scenario', opts.scenario)
  if (opts.idempotencyKey) headers.set('Idempotency-Key', opts.idempotencyKey)
  return headers
}

/** HTTP status → an `ApiError` for the cases where the body was not one. */
const STATUS_FALLBACK: Record<number, [ApiErrorKind, string, string]> = {
  401: ['network', 'Your session has expired or you are not signed in.', 'Sign in to continue.'],
  403: ['network', 'That action is not permitted.', 'Reload the page and try again.'],
  404: ['network', 'That resource was not found.', 'Reload the page — it may have been deleted.'],
  413: ['network', 'The request is larger than the server accepts.', 'Upload fewer files at once.'],
  429: ['rate_limited', 'Too many requests.', 'Wait a few seconds and retry.'],
  503: ['llm_unavailable', 'A dependency is unavailable.', 'Retry in a moment.'],
}

function looksLikeApiError(value: unknown): value is ApiError {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.kind === 'string' && typeof candidate.message === 'string'
}

/**
 * Turn a non-2xx response into an `ApiError`.
 *
 * Exported because the SSE reader needs exactly this: a 429 or a 401 on a
 * stream request arrives as a JSON body where an event stream was expected.
 */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (looksLikeApiError(body)) {
    const error = body
    // Trust the header over the body: a proxy may have set it.
    const retryAfter = Number(res.headers.get('Retry-After'))
    return retryAfter > 0 && error.retryAfterSec === undefined
      ? { ...error, retryAfterSec: retryAfter }
      : error
  }
  const [kind, message, action] = STATUS_FALLBACK[res.status] ?? [
    'network',
    'The request could not be completed.',
    'Try again.',
  ]
  return { kind, message, action }
}

/** The raw call. Returns the `Response` untouched — used by the SSE reader. */
export async function send(path: string, opts: RequestOptions = {}): Promise<Response> {
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    headers: buildHeaders(opts),
    // FR-27: the session cookie is the whole auth model, and it is HttpOnly —
    // nothing here ever reads a token.
    credentials: 'include',
  }
  if (opts.signal) init.signal = opts.signal
  if (opts.form) init.body = opts.form
  else if (opts.body !== undefined) init.body = JSON.stringify(opts.body)

  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), init)
  } catch (thrown) {
    if (isAbort(thrown)) throw thrown
    throw new ApiErrorResponse(NETWORK_ERROR, 0)
  }

  if (res.status === 401 && !opts.allowUnauthorized) {
    const error = await errorFromResponse(res)
    onUnauthorized?.(error)
    throw new ApiErrorResponse(error, 401)
  }
  return res
}

/**
 * A JSON call. `T` is whatever the endpoint returns; a 204 resolves to
 * `undefined`, which is why `request<void>` reads correctly at the call sites
 * for delete and logout.
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts)

  if (!res.ok) throw new ApiErrorResponse(await errorFromResponse(res), res.status)

  // 201 on create and 204 on delete are both normal here, so this keys off the
  // body rather than off the status.
  if (res.status === 204 || res.headers.get('Content-Length') === '0') {
    return undefined as T
  }
  const text = await res.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}
