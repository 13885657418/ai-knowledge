import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  BookOpen,
  Bot,
  FlaskConical,
  MessageSquare,
  ScrollText,
} from "lucide-react"

import {
  ChatService,
  DocumentsService,
  KnowledgeBasesService,
  PromptsService,
} from "@/client"
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({ meta: [{ title: "Dashboard - Knowledge Base AI" }] }),
})

const FEATURES = [
  {
    icon: MessageSquare,
    title: "智能问答",
    desc: "RAG 流式问答、引用溯源、Agent 工具调用与拒答。",
    path: "/chat",
  },
  {
    icon: BookOpen,
    title: "知识库",
    desc: "管理知识库与文档，异步切分入库。",
    path: "/knowledge-bases",
  },
  {
    icon: ScrollText,
    title: "Prompt 管理",
    desc: "版本化 Prompt，支持热切换与 A/B。",
    path: "/prompts",
  },
  {
    icon: Bot,
    title: "Agent 工具",
    desc: "查看已注册工具 schema 并直接调试调用。",
    path: "/tools",
  },
  {
    icon: FlaskConical,
    title: "RAG 评估",
    desc: "Hit@k / MRR / 忠实度等离线评估指标。",
    path: "/eval",
  },
] as const

function Dashboard() {
  const { user: currentUser } = useAuth()

  const { data: kbs } = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => KnowledgeBasesService.readKnowledgeBases({ limit: 1 }),
  })
  const { data: docs } = useQuery({
    queryKey: ["documents-count"],
    queryFn: () => DocumentsService.readDocuments({ limit: 1 }),
  })
  const { data: sessions } = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => ChatService.listSessions({ limit: 1 }),
  })
  const { data: prompts } = useQuery({
    queryKey: ["prompts"],
    queryFn: () => PromptsService.listPrompts({ limit: 1 }),
  })

  const stats = [
    { label: "知识库", value: kbs?.count ?? 0 },
    { label: "文档", value: docs?.count ?? 0 },
    { label: "会话", value: sessions?.count ?? 0 },
    { label: "Prompt 配置", value: prompts?.count ?? 0 },
  ]

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="max-w-sm truncate text-2xl font-bold">
          你好，{currentUser?.full_name || currentUser?.email} 👋
        </h1>
        <p className="text-muted-foreground">
          企业知识库 AI 助手 —— RAG + Agent 全链路演示。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardHeader className="pb-2">
              <CardDescription>{s.label}</CardDescription>
              <CardTitle className="text-3xl">{s.value}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

      <div>
        <h2 className="mb-3 text-lg font-semibold">功能模块</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <Link key={f.path} to={f.path}>
              <Card className="h-full transition-colors hover:border-primary">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <f.icon className="h-5 w-5 text-primary" />
                    {f.title}
                  </CardTitle>
                  <CardDescription>{f.desc}</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
