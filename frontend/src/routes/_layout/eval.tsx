import { useMutation } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { FlaskConical, Play } from "lucide-react"
import { useState } from "react"

import { type EvalRunResponse, EvalService } from "@/client"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/eval")({
  component: EvalPage,
  head: () => ({ meta: [{ title: "RAG 评估 - Knowledge Base AI" }] }),
})

type Metric = {
  key: keyof EvalRunResponse
  label: string
  fmt: (v: number) => string
  hint: string
}

const METRICS: Metric[] = [
  {
    key: "hit_rate_at_k",
    label: "Hit@k",
    fmt: (v) => v.toFixed(3),
    hint: "命中率",
  },
  { key: "mrr", label: "MRR", fmt: (v) => v.toFixed(3), hint: "平均倒数排名" },
  {
    key: "recall_at_k",
    label: "Recall@k",
    fmt: (v) => v.toFixed(3),
    hint: "召回率",
  },
  {
    key: "context_precision",
    label: "Context Precision",
    fmt: (v) => v.toFixed(3),
    hint: "上下文精确率",
  },
  {
    key: "avg_relevance",
    label: "答案相关性",
    fmt: (v) => v.toFixed(3),
    hint: "answer relevancy",
  },
  {
    key: "faithfulness",
    label: "忠实度",
    fmt: (v) => v.toFixed(3),
    hint: "faithfulness",
  },
  {
    key: "avg_latency_ms",
    label: "平均延迟",
    fmt: (v) => `${v.toFixed(0)} ms`,
    hint: "latency",
  },
  {
    key: "refusal_rate",
    label: "拒答率",
    fmt: (v) => `${(v * 100).toFixed(1)}%`,
    hint: "refusal rate",
  },
  {
    key: "estimated_cost",
    label: "估算成本",
    fmt: (v) => `$${v.toFixed(6)}`,
    hint: "cost",
  },
]

function EvalPage() {
  const { showErrorToast } = useCustomToast()
  const [topK, setTopK] = useState(4)
  const [promptVersion, setPromptVersion] = useState("")
  const [withGeneration, setWithGeneration] = useState(true)
  const [datasetText, setDatasetText] = useState("")
  const [result, setResult] = useState<EvalRunResponse | null>(null)

  const run = useMutation({
    mutationFn: () => {
      let dataset = null
      if (datasetText.trim()) {
        try {
          dataset = JSON.parse(datasetText)
        } catch {
          throw new Error("标注集不是合法 JSON 数组")
        }
      }
      return EvalService.runEval({
        requestBody: {
          dataset,
          top_k: topK,
          prompt_version: promptVersion || null,
          with_generation: withGeneration,
        },
      })
    },
    onSuccess: (res) => setResult(res),
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">RAG 评估</h1>
        <p className="text-muted-foreground">
          跑一次离线评估，输出 Hit@k / MRR / 忠实度等核心指标（设计文档 7.6 /
          18.3）。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FlaskConical className="h-4 w-4" />
            实验配置
          </CardTitle>
          <CardDescription>
            标注集留空则使用内置 sample_golden.json。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>top_k</Label>
              <Input
                type="number"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Prompt 版本（可选）</Label>
              <Input
                value={promptVersion}
                onChange={(e) => setPromptVersion(e.target.value)}
                placeholder="默认 active"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={withGeneration}
              onChange={(e) => setWithGeneration(e.target.checked)}
              className="accent-primary"
            />
            包含生成阶段评估（忠实度 / 相关性）
          </label>
          <div className="flex flex-col gap-1.5">
            <Label>自定义标注集（JSON 数组，可选）</Label>
            <textarea
              rows={5}
              value={datasetText}
              onChange={(e) => setDatasetText(e.target.value)}
              className="rounded-md border bg-transparent p-3 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              placeholder='[{"query": "...", "expected_chunk_ids": ["..."]}]'
            />
          </div>
          <LoadingButton
            loading={run.isPending}
            onClick={() => run.mutate()}
            className="w-fit"
          >
            <Play className="mr-2 h-4 w-4" />
            运行评估
          </LoadingButton>
        </CardContent>
      </Card>

      {result && (
        <>
          <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-3">
            {METRICS.map((m) => (
              <Card key={m.key}>
                <CardHeader className="pb-2">
                  <CardDescription>{m.hint}</CardDescription>
                  <CardTitle className="text-2xl">
                    {m.fmt(Number(result[m.key] ?? 0))}
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-0 text-sm text-muted-foreground">
                  {m.label}
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                实验快照（{result.num_items} 条样本）
              </CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="max-h-60 overflow-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(result.config, null, 2)}
              </pre>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
