import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Play, Wrench } from "lucide-react"
import { useMemo, useState } from "react"

import { type ToolSchema, ToolsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"

export const Route = createFileRoute("/_layout/tools")({
  component: ToolsPage,
  head: () => ({ meta: [{ title: "Agent 工具 - Knowledge Base AI" }] }),
})

function toolName(t: ToolSchema): string {
  return t.function?.name ?? t.name ?? "unknown"
}
function toolDesc(t: ToolSchema): string {
  return t.function?.description ?? t.description ?? ""
}

function ToolsPage() {
  const { showErrorToast } = useCustomToast()
  const { data, isLoading } = useQuery({
    queryKey: ["tools"],
    queryFn: () => ToolsService.listTools(),
  })

  const tools = data?.tools ?? []
  const [selected, setSelected] = useState("")
  const [argsText, setArgsText] = useState("{}")
  const [result, setResult] = useState<string>("")

  const selectedSchema = useMemo(
    () => tools.find((t) => toolName(t) === selected),
    [tools, selected],
  )

  const run = useMutation({
    mutationFn: () => {
      let args: Record<string, unknown> = {}
      try {
        args = argsText.trim() ? JSON.parse(argsText) : {}
      } catch {
        throw new Error("参数不是合法 JSON")
      }
      return ToolsService.runTool({
        requestBody: { tool_name: selected, args },
      })
    },
    onSuccess: (res) => {
      setResult(JSON.stringify(res.result, null, 2))
    },
    onError: (e: Error) => {
      const detail = (e as { body?: { detail?: string } }).body?.detail
      showErrorToast(detail || e.message)
    },
  })

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent 工具</h1>
        <p className="text-muted-foreground">
          已注册工具的 function-calling schema，可不经 Agent 循环直接调试调用。
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* 工具列表 */}
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            已注册工具 ({tools.length})
          </h2>
          {isLoading ? (
            <p className="text-muted-foreground">加载中…</p>
          ) : tools.length ? (
            tools.map((t) => (
              <Card key={toolName(t)}>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Wrench className="h-4 w-4" />
                    {toolName(t)}
                  </CardTitle>
                  <CardDescription>{toolDesc(t)}</CardDescription>
                </CardHeader>
                {t.function?.parameters && (
                  <CardContent>
                    <pre className="max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs">
                      {JSON.stringify(t.function.parameters, null, 2)}
                    </pre>
                  </CardContent>
                )}
              </Card>
            ))
          ) : (
            <p className="text-muted-foreground">暂无已注册工具。</p>
          )}
        </div>

        {/* 直调面板 */}
        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="text-base">直接调用</CardTitle>
            <CardDescription>POST /tools/run</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>选择工具</Label>
              <Select value={selected} onValueChange={setSelected}>
                <SelectTrigger>
                  <SelectValue placeholder="选择一个工具" />
                </SelectTrigger>
                <SelectContent>
                  {tools.map((t) => (
                    <SelectItem key={toolName(t)} value={toolName(t)}>
                      {toolName(t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {selectedSchema && (
              <Badge variant="outline" className="w-fit">
                {toolDesc(selectedSchema)}
              </Badge>
            )}
            <div className="flex flex-col gap-1.5">
              <Label>参数 (JSON)</Label>
              <Textarea
                rows={6}
                value={argsText}
                onChange={(e) => setArgsText(e.target.value)}
                className="font-mono text-xs"
                placeholder='{"query": "..."}'
              />
            </div>
            <LoadingButton
              loading={run.isPending}
              disabled={!selected}
              onClick={() => run.mutate()}
            >
              <Play className="mr-2 h-4 w-4" />
              执行
            </LoadingButton>
            {result && (
              <div className="flex flex-col gap-1.5">
                <Label>返回结果</Label>
                <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
                  {result}
                </pre>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
