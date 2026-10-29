import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'

export function NotFoundPage() {
  return (
    <div className="grid min-h-dvh place-items-center bg-canvas px-4">
      <EmptyState
        icon={<Compass className="size-4" />}
        title="That page does not exist"
        body="The link may be stale, or the conversation or document it pointed at may have been deleted."
        action={
          <Button variant="primary" size="sm" asChild>
            <Link to="/">Back to Legal Lens</Link>
          </Button>
        }
      />
    </div>
  )
}
