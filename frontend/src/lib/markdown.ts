/**
 * Hide markdown syntax that has not finished arriving.
 *
 * Tokens stream a few words at a time, so `**Yes — assignment is` sits on
 * screen with raw asterisks until its closing `**` lands. Every answer opens
 * with a bold sentence, which made that the first thing a reader saw.
 *
 * Strategy: mask out every *complete* construct, then remove whatever opening
 * markers are left over — those are the ones still waiting for a closer.
 * Completed syntax is untouched, so the finished answer renders identically to
 * an unmasked one.
 */
export function maskStreamingMarkdown(md: string): string {
  // Private-use sentinels: valid in a regex, and they cannot occur in
  // document text, so nothing real is ever mistaken for a placeholder.
  const stash: string[] = []
  const keep = (s: string) => {
    stash.push(s)
    return `\uE000${stash.length - 1}\uE001`
  }

  let out = md

  // Protect things that legitimately contain a marker: list bullets, thematic
  // breaks, and table rows, so their characters are never read as emphasis.
  out = out.replace(/^[ \t]*[*+-][ \t]+/gm, keep)
  out = out.replace(/^[ \t]*(?:\*{3,}|-{3,}|_{3,})[ \t]*$/gm, keep)

  // Stash every complete construct, longest-first so `**` wins over `*`.
  out = out.replace(/`[^`\n]*`/g, keep)
  out = out.replace(/\[\[\d+\]\]/g, keep)
  out = out.replace(/\[[^\]\n]*\]\([^)\n]*\)/g, keep)
  out = out.replace(/\*\*[^*\n]+\*\*/g, keep)
  out = out.replace(/__[^_\n]+__/g, keep)
  out = out.replace(/\*[^*\n]+\*/g, keep)

  // Whatever marker survives is an opener with no closer yet — drop it.
  out = out.replace(/\*\*|\*|`|__/g, '')
  out = out.replace(/\[\[\d*/g, '')

  // A heading or table row whose content has not arrived yet.
  out = out.replace(/(^|\n)#{1,6}[ \t]*$/g, '$1')
  out = out.replace(/(^|\n)\|[ \t]*$/g, '$1')

  return out.replace(/\uE000(\d+)\uE001/g, (_, i) => stash[Number(i)])
}
