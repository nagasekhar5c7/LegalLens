import type { AssistantTurn, StreamEvent } from './types'
import { errorFromResponse, isAbort, send, type RequestOptions } from './http'

/**
 * Server-sent events over POST.
 *
 * `EventSource` cannot do this: it is GET-only, and the request body carries
 * `scope`, `mode` and `draftParams`. So this is `fetch` plus a line parser —
 * which the backend anticipated by writing the discriminator *inside* the JSON
 * as well as on the `event:` line, so we can ignore the `event:` line entirely
 * and switch on `payload.type` exactly as `types.ts` declares.
 *
 * Four things the parser has to get right, each of which is a real failure:
 *
 *   * **A chunk boundary can fall mid-frame.** Hence the buffer that survives
 *     across reads.
 *   * **A non-200 is not a stream.** A 429 or a 401 arrives as a JSON
 *     `ApiError` body; it becomes one `error` event so the consumer needs no
 *     second code path.
 *   * **Keep-alive comments arrive every 15 s** so a three-minute summary
 *     (NFR-3) survives an idle-connection reaper. A parser that chokes on a
 *     `:` line breaks precisely the case they exist for.
 *   * **An abort must propagate.** `stop()` has to reach the server, or the
 *     turn is recorded as `complete` when the user cut it off (NFR-7).
 */
export interface StreamOptions extends Omit<RequestOptions, 'method'> {
  /** Called once with the response, before the first event. */
  onOpen?: (res: Response) => void
  /**
   * An idempotent replay returns the stored turn as JSON instead of a stream.
   * Unreachable with a fresh key per send, which is what the client does — but
   * a JSON body where a stream was expected should not be silently dropped.
   */
  onReplay?: (turn: AssistantTurn) => void
}

const FRAME = /\r?\n\r?\n/

export async function* streamEvents(
  path: string,
  opts: StreamOptions = {},
): AsyncGenerator<StreamEvent> {
  const res = await send(path, { ...opts, method: 'POST' })
  opts.onOpen?.(res)

  if (!res.ok) {
    yield { type: 'error', error: await errorFromResponse(res) }
    return
  }

  const contentType = res.headers.get('Content-Type') ?? ''
  if (!contentType.includes('text/event-stream')) {
    const turn = (await res.json()) as AssistantTurn
    opts.onReplay?.(turn)
    return
  }

  if (!res.body) {
    yield {
      type: 'error',
      error: {
        kind: 'network',
        message: 'The response carried no body, so no answer could be read.',
        action: 'Retry.',
      },
    }
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Everything up to the last frame separator is complete; the remainder
      // may be half an event, so it stays in the buffer.
      const blocks = buffer.split(FRAME)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const event = parseFrame(block)
        if (event) yield event
      }
    }

    // A stream that ends without a trailing blank line still has one event.
    const tail = parseFrame(buffer + decoder.decode())
    if (tail) yield tail
  } catch (thrown) {
    if (isAbort(thrown)) throw thrown
    yield {
      type: 'error',
      error: {
        kind: 'network',
        message: 'The connection dropped while the answer was streaming.',
        action: 'Retry — anything already written has been kept as incomplete.',
      },
    }
  } finally {
    // Cancel rather than release: on an abort the body is still open, and a
    // dangling reader keeps the socket (and the server's generator) alive.
    await reader.cancel().catch(() => undefined)
  }
}

/**
 * One SSE frame → one event, or null for a frame that carries no data:
 * keep-alive comments, the opening `retry:` hint, and `event:`-only blocks.
 */
function parseFrame(block: string): StreamEvent | null {
  if (!block.trim()) return null

  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    // A comment. The keep-alive is `: keep-alive`.
    if (line.startsWith(':')) continue
    if (!line.startsWith('data:')) continue
    // Per the SSE grammar, exactly one leading space after the colon is
    // stripped — any further whitespace is part of the value.
    const value = line.slice(5)
    data.push(value.startsWith(' ') ? value.slice(1) : value)
  }

  if (data.length === 0) return null
  try {
    return JSON.parse(data.join('\n')) as StreamEvent
  } catch {
    // A truncated final frame. Dropping it is right: the turn is already being
    // recorded as incomplete by the server.
    return null
  }
}
