import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FileUp, Loader2, Plus, Upload } from "lucide-react"
import { useCallback, useRef, useState } from "react"

import { type DocumentCreate, DocumentsWriteService, OpenAPI } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const AddDocument = ({ knowledgeBaseId }: { knowledgeBaseId: string }) => {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [form, setForm] = useState({
    file_name: "",
    file_type: "md",
    chunk_strategy: "fixed",
    summary: "",
  })

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["knowledge-base-documents", knowledgeBaseId],
    })

  // 手动登记文档（不上传文件）
  const registerMutation = useMutation({
    mutationFn: (data: DocumentCreate) =>
      DocumentsWriteService.createDocument({ requestBody: data }),
    onSuccess: () => {
      showSuccessToast("文档已登记")
      setForm({
        file_name: "",
        file_type: "md",
        chunk_strategy: "fixed",
        summary: "",
      })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: invalidate,
  })

  // 文件上传
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const token = localStorage.getItem("access_token") || ""
      const formData = new FormData()
      formData.append("file", file)
      const resp = await fetch(
        `${OpenAPI.BASE}/api/v1/documents/upload?knowledge_base_id=${knowledgeBaseId}&chunk_strategy=${form.chunk_strategy}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        },
      )
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(
          (err as { detail?: string }).detail || `上传失败 (${resp.status})`,
        )
      }
      return resp.json()
    },
    onSuccess: (_data, file) => {
      showSuccessToast(`「${file.name}」上传成功`)
      setFiles((prev) => prev.filter((f) => f !== file))
    },
    onError: (e: Error) => showErrorToast(e.message),
    onSettled: invalidate,
  })

  const uploadAll = () => {
    for (const f of files) {
      uploadMutation.mutate(f)
    }
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const dropped = Array.from(e.dataTransfer.files)
    if (dropped.length) setFiles((prev) => [...prev, ...dropped])
  }, [])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    if (selected.length) setFiles((prev) => [...prev, ...selected])
    e.target.value = ""
  }

  const removeFile = (file: File) => {
    setFiles((prev) => prev.filter((f) => f !== file))
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          添加文档
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>添加文档</SheetTitle>
          <SheetDescription>
            上传文件或手动登记文档元数据，后端异步切分入库。
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4">
          {/* 文件上传区 */}
          <div className="flex flex-col gap-3">
            <Label className="text-sm font-medium">文件上传</Label>
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ")
                  fileInputRef.current?.click()
              }}
              role="button"
              tabIndex={0}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
                dragOver
                  ? "border-primary bg-primary/5"
                  : "border-muted-foreground/25 hover:border-primary/50"
              }`}
            >
              <Upload className="mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">拖拽文件到此处，或点击选择</p>
              <p className="text-xs text-muted-foreground">
                支持 PDF、Markdown、TXT、DOCX 等
              </p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileSelect}
              accept=".pdf,.md,.txt,.docx,.doc,.html,.csv,.json"
            />

            {files.length > 0 && (
              <div className="flex flex-col gap-2">
                {files.map((f) => (
                  <div
                    key={`${f.name}-${f.size}`}
                    className="flex items-center gap-2 rounded-md border px-3 py-2"
                  >
                    <FileUp className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="flex-1 truncate text-sm">{f.name}</span>
                    <Badge variant="secondary" className="shrink-0">
                      {(f.size / 1024).toFixed(1)} KB
                    </Badge>
                    <button
                      type="button"
                      onClick={() => removeFile(f)}
                      className="text-xs text-destructive hover:underline"
                    >
                      移除
                    </button>
                  </div>
                ))}
                <div className="flex items-center gap-3">
                  <div className="flex flex-col gap-1.5">
                    <Label className="text-xs">切分策略</Label>
                    <Select
                      value={form.chunk_strategy}
                      onValueChange={(v) =>
                        setForm({ ...form, chunk_strategy: v })
                      }
                    >
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="fixed">fixed</SelectItem>
                        <SelectItem value="markdown">markdown</SelectItem>
                        <SelectItem value="paragraph">paragraph</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <LoadingButton
                    className="ml-auto mt-auto"
                    loading={uploadMutation.isPending}
                    onClick={uploadAll}
                  >
                    {uploadMutation.isPending ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Upload className="mr-2 h-4 w-4" />
                    )}
                    上传全部 ({files.length})
                  </LoadingButton>
                </div>
              </div>
            )}
          </div>

          {/* 分割线 */}
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">或手动登记</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          {/* 手动登记表单 */}
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>
                文件名 <span className="text-destructive">*</span>
              </Label>
              <Input
                value={form.file_name}
                onChange={(e) =>
                  setForm({ ...form, file_name: e.target.value })
                }
                placeholder="handbook.md"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label>类型</Label>
                <Input
                  value={form.file_type}
                  onChange={(e) =>
                    setForm({ ...form, file_type: e.target.value })
                  }
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>切分策略</Label>
                <Select
                  value={form.chunk_strategy}
                  onValueChange={(v) => setForm({ ...form, chunk_strategy: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fixed">fixed</SelectItem>
                    <SelectItem value="markdown">markdown</SelectItem>
                    <SelectItem value="paragraph">paragraph</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>摘要</Label>
              <Textarea
                rows={2}
                value={form.summary}
                onChange={(e) => setForm({ ...form, summary: e.target.value })}
              />
            </div>
          </div>
        </div>

        <SheetFooter>
          <SheetClose asChild>
            <Button variant="outline">关闭</Button>
          </SheetClose>
          <LoadingButton
            loading={registerMutation.isPending}
            disabled={!form.file_name}
            onClick={() =>
              registerMutation.mutate({
                ...form,
                knowledge_base_id: knowledgeBaseId,
              })
            }
          >
            登记文档
          </LoadingButton>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

export default AddDocument
