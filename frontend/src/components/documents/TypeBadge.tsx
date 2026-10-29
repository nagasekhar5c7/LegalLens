import { ChevronDown, Gavel, FileSignature, FileQuestion, Scale } from 'lucide-react'
import type { DocType } from '@/api/types'
import { DOC_TYPE_LABEL } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Menu, MenuContent, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuTrigger } from '@/components/ui/Menu'
import { Tooltip } from '@/components/ui/Floating'

const TYPE_ICON: Record<DocType, React.ReactNode> = {
  contract: <FileSignature aria-hidden className="size-3" />,
  opinion: <Scale aria-hidden className="size-3" />,
  pleading: <Gavel aria-hidden className="size-3" />,
  other: <FileQuestion aria-hidden className="size-3" />,
}

/**
 * FR-5: show the classification, keep its provenance visible, allow override.
 *
 * The type decides which summary template runs, so a wrong classification
 * produces a structurally wrong summary — which is why low confidence is
 * surfaced rather than hidden behind an average.
 */
export function TypeBadge({
  type,
  source,
  confidence,
  onChange,
}: {
  type: DocType
  source: 'detected' | 'overridden'
  confidence: number
  onChange?: (t: DocType) => void
}) {
  const lowConfidence = source === 'detected' && confidence < 0.75
  const help =
    source === 'overridden'
      ? 'Set by you, overriding the classifier.'
      : `Detected by the classifier at ${Math.round(confidence * 100)}% confidence.${
          lowConfidence ? ' Low — worth confirming, since the type picks the summary template.' : ''
        }`

  const badge = (
    <Badge
      tone={lowConfidence ? 'caution' : source === 'overridden' ? 'navy' : 'neutral'}
      className={onChange ? 'cursor-pointer gap-1 hover:brightness-[0.97]' : 'gap-1'}
    >
      {TYPE_ICON[type]}
      {DOC_TYPE_LABEL[type]}
      {source === 'overridden' && <span className="opacity-60">· set</span>}
      {lowConfidence && <span className="opacity-70">· {Math.round(confidence * 100)}%</span>}
      {onChange && <ChevronDown aria-hidden className="size-2.5 opacity-50" />}
    </Badge>
  )

  if (!onChange) return <Tooltip content={help}>{badge}</Tooltip>

  return (
    <Menu>
      <Tooltip content={help}>
        <MenuTrigger asChild>{badge}</MenuTrigger>
      </Tooltip>
      <MenuContent align="start">
        <MenuLabel>Document type</MenuLabel>
        <MenuRadioGroup value={type} onValueChange={(v) => onChange(v as DocType)}>
          {(['contract', 'opinion', 'pleading', 'other'] as DocType[]).map((t) => (
            <MenuRadioItem key={t} value={t}>
              {TYPE_ICON[t]}
              {DOC_TYPE_LABEL[t]}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <p className="px-2 pb-1 pt-1.5 text-[10px] leading-snug text-ink-3">
          The type selects the summary template, so an override changes what a summary extracts.
        </p>
      </MenuContent>
    </Menu>
  )
}
