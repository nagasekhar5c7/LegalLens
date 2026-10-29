import { useEffect, useRef } from 'react'

export const MOD = typeof navigator !== 'undefined' && /Mac|iP/.test(navigator.platform) ? '⌘' : 'Ctrl'

interface Options {
  /** Fire even when focus is inside an input or textarea. */
  allowInEditable?: boolean
  enabled?: boolean
}

function isEditable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable
}

/**
 * Declarative key binding. `combo` examples: "mod+k", "mod+enter", "/",
 * "alt+ArrowUp", "shift+?". Comparison is case-insensitive on `key`.
 */
export function useHotkey(
  combo: string,
  handler: (e: KeyboardEvent) => void,
  { allowInEditable = false, enabled = true }: Options = {},
) {
  const ref = useRef(handler)
  ref.current = handler

  useEffect(() => {
    if (!enabled) return
    const parts = combo.toLowerCase().split('+')
    const key = parts[parts.length - 1]
    const needMod = parts.includes('mod')
    const needShift = parts.includes('shift')
    const needAlt = parts.includes('alt')

    function onKeyDown(e: KeyboardEvent) {
      if (!allowInEditable && isEditable(e.target)) return
      const mod = e.metaKey || e.ctrlKey
      if (needMod !== mod) return
      if (needShift !== e.shiftKey) return
      if (needAlt !== e.altKey) return
      if (e.key.toLowerCase() !== key) return
      ref.current(e)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [combo, allowInEditable, enabled])
}

/** The shortcut map, rendered in Settings so it is documented in one place. */
export const SHORTCUTS: { keys: string; label: string; group: string }[] = [
  { keys: `${MOD} K`, label: 'Command palette', group: 'Navigation' },
  { keys: `${MOD} ⇧ O`, label: 'New chat', group: 'Navigation' },
  { keys: `${MOD} L`, label: 'Document library', group: 'Navigation' },
  { keys: `${MOD} U`, label: 'Upload documents', group: 'Navigation' },
  { keys: '/', label: 'Focus the prompt', group: 'Navigation' },
  { keys: `${MOD} ↵`, label: 'Send', group: 'Composing' },
  { keys: '↵', label: 'New line', group: 'Composing' },
  { keys: `${MOD} 1 – 4`, label: 'Auto · Ask · Summary · Draft', group: 'Composing' },
  { keys: '⌥ ↑ / ↓', label: 'Cycle citations in the last answer', group: 'Verifying' },
  { keys: '⌥ ↵', label: 'Open the focused citation', group: 'Verifying' },
  { keys: '[', label: 'Toggle sidebar', group: 'Panels' },
  { keys: ']', label: 'Toggle evidence panel', group: 'Panels' },
  { keys: 'Esc', label: 'Close panel or dialog', group: 'Panels' },
]
