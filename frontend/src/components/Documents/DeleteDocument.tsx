import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Trash2 } from "lucide-react"

import { DocumentsWriteService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const DeleteDocument = ({
  id,
  knowledgeBaseId,
  fileName,
}: {
  id: string
  knowledgeBaseId: string
  fileName: string
}) => {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () => DocumentsWriteService.deleteDocument({ id }),
    onSuccess: () => showSuccessToast("文档已删除"),
    onError: handleError.bind(showErrorToast),
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: ["knowledge-base-documents", knowledgeBaseId],
      }),
  })

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" className="text-destructive">
          <Trash2 className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>删除文档</DialogTitle>
          <DialogDescription>
            确认删除「{fileName}」？相关 chunk 将级联删除，此操作不可恢复。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default DeleteDocument
