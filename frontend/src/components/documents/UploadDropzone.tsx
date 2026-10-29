import { useRef, useState } from 'react'
import { FileUp, UploadCloud } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Button } from '@/components/ui/Button'
import { useConfig } from '@/store/useConfig'

/** FR-1: multi-file drag-drop with a picker fallback, no per-file ceremony. */
export function UploadDropzone({
  onFiles,
  compact,
}: {
  onFiles: (files: File[]) => void
  compact?: boolean
}) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  // FR-2: name the real limit. These are the server's numbers, not a copy.
  const limits = useConfig((s) => s.config)
  const extensions = limits.acceptedExtensions
    .map((e) => e.replace('.', '').toUpperCase())
    .join(', ')

  function take(list: FileList | null) {
    if (!list || list.length === 0) return
    onFiles(Array.from(list))
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        take(e.dataTransfer.files)
      }}
      className={cn(
        'rounded-card border-2 border-dashed text-center transition-colors',
        compact ? 'px-4 py-5' : 'px-6 py-9',
        over ? 'border-navy-600 bg-navy-50' : 'border-line-strong bg-canvas',
      )}
    >
      <input
        ref={input}
        type="file"
        multiple
        accept={limits.acceptedExtensions.join(',')}
        className="sr-only"
        onChange={(e) => {
          take(e.target.files)
          e.target.value = ''
        }}
      />

      <span
        aria-hidden
        className={cn(
          'mx-auto grid place-items-center rounded-full',
          compact ? 'size-8' : 'size-10',
          over ? 'bg-navy-100 text-navy-700' : 'bg-sunken text-ink-3',
        )}
      >
        {over ? (
          <UploadCloud className={compact ? 'size-4' : 'size-5'} />
        ) : (
          <FileUp className={compact ? 'size-4' : 'size-5'} />
        )}
      </span>

      <p className={cn('font-semibold text-ink', compact ? 'mt-2 text-xs' : 'mt-3 text-sm')}>
        {over ? 'Drop to add these documents' : 'Drag documents here'}
      </p>
      <p className="mx-auto mt-1 max-w-sm text-2xs leading-relaxed text-ink-2">
        {extensions} · up to {Math.round(limits.maxBytes / 1024 / 1024)} MB and{' '}
        {limits.maxPages} pages each · {limits.maxDocuments} documents per workspace. Scanned PDFs
        are read by OCR, and any low-confidence pages are flagged.
      </p>

      <Button
        size="sm"
        variant="secondary"
        className="mt-3"
        onClick={() => input.current?.click()}
      >
        Choose files
      </Button>
    </div>
  )
}
