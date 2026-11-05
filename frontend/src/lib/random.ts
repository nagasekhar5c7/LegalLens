/**
 * A random v4 UUID that also works on plain HTTP.
 *
 * `crypto.randomUUID` exists only in secure contexts (HTTPS or localhost), so on
 * `http://<public IP>` it is undefined and calling it throws, which the send path
 * reports as "Connection lost" without the request ever leaving the browser.
 * `crypto.getRandomValues` is available everywhere, so it is the fallback.
 */
export function randomId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
