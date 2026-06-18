import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { CheckCircle2, Circle, Pencil, Plus } from "lucide-react"
import { useState } from "react"

import {
  type PromptConfigCreate,
  type PromptConfigPublic,
  PromptsService,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/prompts")({
  component: PromptsPage,
  head: () => ({ meta: [{ title: "Prompt 管理 - Knowledge Base AI" }] }),
})

function AddPromptDialog() {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [form, setForm] = useState<PromptConfigCreate>({
    name: "",
    version: "",
    system_prompt: "",
    retrieval_template: "",
    answer_template: "",
    is_active: false,
  })

  const mutation = useMutation({
    mutationFn: (data: PromptConfigCreate) =>
      PromptsService.createPrompt({ requestBody: data }),
    onSuccess: () => {
      showSuccessToast("Prompt 配置已创建")
      setOpen(false)
      setForm({
        name: "",
        version: "",
        system_prompt: "",
        retrieval_template: "",
        answer_template: "",
        is_active: false,
      })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["prompts"] }),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          新建 Prompt
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建 Prompt 配置</DialogTitle>
          <DialogDescription>
            可版本化、可热切换的 Prompt（设计文档 6.8）。
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[60vh] gap-3 overflow-y-auto py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>名称 *</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="default_qa"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>版本 *</Label>
              <Input
                value={form.version}
                onChange={(e) => setForm({ ...form, version: e.target.value })}
                placeholder="v1"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>System Prompt *</Label>
            <Textarea
              rows={4}
              value={form.system_prompt}
              onChange={(e) =>
                setForm({ ...form, system_prompt: e.target.value })
              }
              placeholder="你是企业知识库助手……"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Retrieval Template</Label>
            <Textarea
              rows={3}
              value={form.retrieval_template ?? ""}
              onChange={(e) =>
                setForm({ ...form, retrieval_template: e.target.value })
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Answer Template</Label>
            <Textarea
              rows={3}
              value={form.answer_template ?? ""}
              onChange={(e) =>
                setForm({ ...form, answer_template: e.target.value })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) =>
                setForm({ ...form, is_active: e.target.checked })
              }
              className="accent-primary"
            />
            创建后立即激活
          </label>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <LoadingButton
            loading={mutation.isPending}
            disabled={!form.name || !form.version || !form.system_prompt}
            onClick={() => mutation.mutate(form)}
          >
            保存
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditPromptDialog({ prompt }: { prompt: PromptConfigPublic }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [form, setForm] = useState<PromptConfigCreate>({
    name: prompt.name,
    version: prompt.version,
    system_prompt: prompt.system_prompt,
    retrieval_template: prompt.retrieval_template ?? "",
    answer_template: prompt.answer_template ?? "",
    is_active: prompt.is_active,
  })

  const mutation = useMutation({
    mutationFn: (data: PromptConfigCreate) =>
      PromptsService.updatePrompt({ id: prompt.id, requestBody: data }),
    onSuccess: () => {
      showSuccessToast("Prompt 已修改")
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["prompts"] }),
  })

  function handleOpen() {
    setForm({
      name: prompt.name,
      version: prompt.version,
      system_prompt: prompt.system_prompt,
      retrieval_template: prompt.retrieval_template ?? "",
      answer_template: prompt.answer_template ?? "",
      is_active: prompt.is_active,
    })
    setOpen(true)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={handleOpen}>
        <Pencil className="mr-1 h-3 w-3" />
        修改
      </Button>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>修改 Prompt 配置</DialogTitle>
          <DialogDescription>
            编辑后点保存即可生效。
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[60vh] gap-3 overflow-y-auto py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>名称</Label>
              <Input value={form.name} disabled />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>版本 *</Label>
              <Input
                value={form.version}
                onChange={(e) => setForm({ ...form, version: e.target.value })}
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>System Prompt *</Label>
            <Textarea
              rows={5}
              value={form.system_prompt}
              onChange={(e) =>
                setForm({ ...form, system_prompt: e.target.value })
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Retrieval Template</Label>
            <Textarea
              rows={3}
              value={form.retrieval_template ?? ""}
              onChange={(e) =>
                setForm({ ...form, retrieval_template: e.target.value })
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Answer Template</Label>
            <Textarea
              rows={3}
              value={form.answer_template ?? ""}
              onChange={(e) =>
                setForm({ ...form, answer_template: e.target.value })
              }
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <LoadingButton
            loading={mutation.isPending}
            disabled={!form.version || !form.system_prompt}
            onClick={() => mutation.mutate(form)}
          >
            保存修改
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PromptCard({ prompt }: { prompt: PromptConfigPublic }) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const activate = useMutation({
    mutationFn: () => PromptsService.activatePrompt({ id: prompt.id }),
    onSuccess: () => {
      showSuccessToast(`已激活 ${prompt.name} · ${prompt.version}`)
      queryClient.invalidateQueries({ queryKey: ["prompts"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            {prompt.name}
            <Badge variant="outline">{prompt.version}</Badge>
            {prompt.is_active && (
              <Badge className="bg-green-600 hover:bg-green-600">
                <CheckCircle2 className="mr-1 h-3 w-3" />
                active
              </Badge>
            )}
          </CardTitle>
          {!prompt.is_active && (
            <LoadingButton
              size="sm"
              variant="outline"
              loading={activate.isPending}
              onClick={() => activate.mutate()}
            >
              <Circle className="mr-1 h-3 w-3" />
              激活
            </LoadingButton>
          )}
          <EditPromptDialog prompt={prompt} />
        </div>
        <CardDescription className="line-clamp-2 whitespace-pre-wrap">
          {prompt.system_prompt}
        </CardDescription>
      </CardHeader>
      {(prompt.retrieval_template || prompt.answer_template) && (
        <CardContent className="space-y-2 text-xs text-muted-foreground">
          {prompt.retrieval_template && (
            <div>
              <span className="font-medium">retrieval:</span>{" "}
              <span className="line-clamp-1">{prompt.retrieval_template}</span>
            </div>
          )}
          {prompt.answer_template && (
            <div>
              <span className="font-medium">answer:</span>{" "}
              <span className="line-clamp-1">{prompt.answer_template}</span>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function PromptsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["prompts"],
    queryFn: () => PromptsService.listPrompts({ limit: 100 }),
  })

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Prompt 管理</h1>
          <p className="text-muted-foreground">
            版本化管理与热切换问答 Prompt，同名仅一个 active。
          </p>
        </div>
        <AddPromptDialog />
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">加载中…</p>
      ) : data?.data.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {data.data.map((p) => (
            <PromptCard key={p.id} prompt={p} />
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">
          暂无 Prompt 配置，点击右上角新建。
        </p>
      )}
    </div>
  )
}
