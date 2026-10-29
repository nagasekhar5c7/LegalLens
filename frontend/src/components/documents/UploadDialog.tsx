import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/Button'
import { Dialog, DialogContent } from '@/components/ui/Overlay'
import { useUi } from '@/store/useUi'
import { useWorkspace } from '@/store/useWorkspace'
import { UploadDropzone } from './UploadDropzone'
import { UploadQueue } from './UploadQueue'

/** Upload without leaving the conversation — FR-1's "no per-file ceremony". */
export function UploadDialog() {
  const { uploadOpen, setUploadOpen } = useUi()
  const { upload, uploads, clearUploads } = useWorkspace()

  return (
    <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
      <DialogContent
        title="Add documents"
        description="Documents become searchable once indexing finishes. Nothing is retrievable before then."
        size="md"
        footer={
          <>
            <Button size="sm" variant="ghost" asChild>
              <Link to="/library" onClick={() => setUploadOpen(false)}>
                Open the library
              </Link>
            </Button>
            <Button size="sm" variant="primary" onClick={() => setUploadOpen(false)}>
              Done
            </Button>
          </>
        }
      >
        <div className="space-y-3 p-4">
          <UploadDropzone onFiles={(files) => void upload(files)} />
          <UploadQueue rows={uploads} onClear={clearUploads} />
        </div>
      </DialogContent>
    </Dialog>
  )
}
