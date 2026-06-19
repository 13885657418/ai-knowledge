import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  Bot,
  FileText,
  Loader2,
  MessageSquarePlus,
  Send,
  Sparkles,
  Square,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"

import {
  type AskResponse,
  askStream,
  ChatService,
  type Citation,
  KnowledgeBasesService,
  PromptsService,
  type StreamEvent,
  type Usage,
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/chat")({
  component: ChatPage,
  head: () => ({ meta: [{ title: "智能问答 - Knowledge Base AI" }] }),
})

type UiMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  citations?: Citation[]
  usage?: Usage
  isRefused?: boolean
  streaming?: boolean
}

function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null
  return (
    <div className="mt-3 flex flex-col gap-2 border-t pt-3">
      <span className="text-xs font-medium text-muted-foreground">
        引用来源 ({citations.length})
      </span>
      {citations.map((c, i) => (
        <div
          key={c.chunk_id || i}
          className="rounded-md bg-muted/50 px-3 py-2 text-xs"
        >
          <div className="mb-1 flex items-center gap-2 font-medium">
            <FileText className="h-3 w-3" />
            <span className="truncate">{c.document || "未知文档"}</span>
            <Badge variant="outline" className="ml-auto shrink-0">
              chunk #{c.chunk_index}
            </Badge>
          </div>
          <p className="line-clamp-3 text-muted-foreground">{c.preview}</p>
        </div>
      ))}
    </div>
  )
}

function UsageBadges({ usage }: { usage?: Usage }) {
  if (!usage) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
      <Badge variant="secondary">prompt {usage.prompt_tokens}</Badge>
      <Badge variant="secondary">completion {usage.completion_tokens}</Badge>
      <Badge variant="secondary">
        ${Number(usage.estimated_cost ?? 0).toFixed(6)}
      </Badge>
    </div>
  )
}

function ChatPage() {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()

  const [sessionId, setSessionId] = useState<string>("")
  const [kbId, setKbId] = useState<string>("")
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState("")
  const [topK, setTopK] = useState(4)
  const [useAgent, setUseAgent] = useState(false)
  const [useStream, setUseStream] = useState(true)
  const [promptVersion, setPromptVersion] = useState("")
  const [isAsking, setIsAsking] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: kbs } = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => KnowledgeBasesService.readKnowledgeBases({ limit: 100 }),
  })
  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => ChatService.listSessions({ limit: 100 }),
  })
  const { data: prompts } = useQuery({
    queryKey: ["prompts"],
    queryFn: () => PromptsService.listPrompts({ limit: 100 }),
  })

  // 自动滚到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const createSession = useMutation({
    mutationFn: () =>
      ChatService.createSession({
        requestBody: { knowledge_base_id: kbId, title: "新会话" },
      }),
    onSuccess: (s) => {
      setSessionId(s.id)
      setMessages([])
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] })
      showSuccessToast("会话已创建")
    },
    onError: handleError.bind(showErrorToast),
  })

  const loadSession = async (id: string) => {
    setSessionId(id)
    try {
      const res = await ChatService.listMessages({ id })
      setMessages(
        res.data.map((m) => ({
          id: m.id,
          role: m.role === "assistant" ? "assistant" : "user",
          content: m.content,
        })),
      )
    } catch (e) {
      handleError.call(showErrorToast, e as never)
    }
  }

  const stopStream = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsAsking(false)
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    )
  }

  const send = async () => {
    const query = input.trim()
    if (!query || !sessionId || isAsking) return
    setInput("")
    const userMsg: UiMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      content: query,
    }
    setMessages((prev) => [...prev, userMsg])
    setIsAsking(true)

    const selectedPrompt = prompts?.data.find((p) => p.id === promptVersion)
    const body = {
      query,
      top_k: topK,
      use_agent: useAgent,
      prompt_version: selectedPrompt?.version ?? null,
    }

    if (useStream && !useAgent) {
      const assistantId = `a-${Date.now()}`
      setMessages((prev) => [
        ...prev,
        { id: assistantId, role: "assistant", content: "", streaming: true },
      ])
      const ctrl = new AbortController()
      abortRef.current = ctrl
      try {
        await askStream(sessionId, body, {
          signal: ctrl.signal,
          onEvent: (evt: StreamEvent) => {
            if (evt.event === "retrieval") {
              const d = evt.data as {
                citations?: Citation[]
                is_refused?: boolean
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, citations: d.citations, isRefused: d.is_refused }
                    : m,
                ),
              )
            } else if (evt.event === "token") {
              const d = evt.data as { token?: string; text?: string; content?: string }
              const piece = d.token ?? d.text ?? d.content ?? ""
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: m.content + piece }
                    : m,
                ),
              )
            } else if (evt.event === "done") {
              const d = evt.data as { usage?: Usage; is_refused?: boolean }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        usage: d.usage,
                        isRefused: d.is_refused ?? m.isRefused,
                        streaming: false,
                      }
                    : m,
                ),
              )
            } else if (evt.event === "error") {
              const d = evt.data as { detail?: string }
              showErrorToast(d.detail || "流式问答出错")
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, streaming: false } : m,
                ),
              )
            }
          },
        })
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          showErrorToast((e as Error).message)
        }
      } finally {
        setIsAsking(false)
        abortRef.current = null
        setMessages((prev) =>
          prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
        )
      }
    } else {
      // 非流式（含 Agent 模式）
      try {
        const res: AskResponse = await ChatService.ask({
          id: sessionId,
          requestBody: body,
        })
        setMessages((prev) => [
          ...prev,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: res.answer,
            citations: res.citations,
            usage: res.usage,
            isRefused: res.is_refused,
          },
        ])
      } catch (e) {
        handleError.call(showErrorToast, e as never)
      } finally {
        setIsAsking(false)
      }
    }
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* 左侧：会话与参数 */}
      <div className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">会话设置</CardTitle>
            <CardDescription>选择知识库并新建会话</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">知识库</Label>
              <Select value={kbId} onValueChange={setKbId}>
                <SelectTrigger>
                  <SelectValue placeholder="选择知识库" />
                </SelectTrigger>
                <SelectContent>
                  {kbs?.data.map((kb) => (
                    <SelectItem key={kb.id} value={kb.id}>
                      {kb.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              disabled={!kbId || createSession.isPending}
              onClick={() => createSession.mutate()}
            >
              <MessageSquarePlus className="mr-2 h-4 w-4" />
              新建会话
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">检索参数</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">top_k：{topK}</Label>
              <input
                type="range"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
                className="w-full accent-primary"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">Prompt 版本</Label>
              <Select
                value={promptVersion || "__default__"}
                onValueChange={(v) =>
                  setPromptVersion(v === "__default__" ? "" : v)
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="默认（active）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">默认（active）</SelectItem>
                  {prompts?.data.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name} · {p.version}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={useAgent}
                onChange={(e) => setUseAgent(e.target.checked)}
                className="accent-primary"
              />
              <Bot className="h-4 w-4" />
              Agent 模式（ReAct 工具循环）
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={useStream}
                disabled={useAgent}
                onChange={(e) => setUseStream(e.target.checked)}
                className="accent-primary"
              />
              <Sparkles className="h-4 w-4" />
              SSE 流式输出
            </label>
            {useAgent && (
              <p className="text-[11px] text-muted-foreground">
                Agent 模式走非流式接口。
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="flex-1">
          <CardHeader>
            <CardTitle className="text-base">历史会话</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {sessions?.data.length ? (
              sessions.data.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  onClick={() => loadSession(s.id)}
                  className={`truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${
                    s.id === sessionId ? "bg-muted font-medium" : ""
                  }`}
                >
                  {s.title || s.id.slice(0, 8)}
                </button>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">暂无会话</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 右侧：对话区 */}
      <div className="flex flex-1 flex-col rounded-xl border bg-card">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-6">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground">
              <Sparkles className="mb-3 h-10 w-10" />
              <p className="font-medium">
                {sessionId ? "开始提问吧" : "请先选择知识库并新建会话"}
              </p>
              <p className="text-sm">
                支持流式输出、引用溯源、Agent 工具调用与拒答。
              </p>
            </div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={`flex ${
                  m.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
                    m.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted"
                  }`}
                >
                  {m.isRefused && (
                    <Badge variant="destructive" className="mb-2">
                      已拒答
                    </Badge>
                  )}
                  <div className="whitespace-pre-wrap break-words">
                    {m.content}
                    {m.streaming && (
                      <Loader2 className="ml-1 inline h-3 w-3 animate-spin" />
                    )}
                  </div>
                  {m.role === "assistant" && (
                    <>
                      <CitationList citations={m.citations || []} />
                      <UsageBadges usage={m.usage} />
                    </>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="border-t p-4">
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder={
                sessionId
                  ? "输入问题，Enter 发送，Shift+Enter 换行"
                  : "请先新建会话"
              }
              disabled={!sessionId || isAsking}
              className="min-h-[44px] resize-none"
              rows={1}
            />
            {isAsking && abortRef.current ? (
              <Button variant="outline" size="icon" onClick={stopStream}>
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                size="icon"
                disabled={!sessionId || !input.trim() || isAsking}
                onClick={send}
              >
                {isAsking ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
