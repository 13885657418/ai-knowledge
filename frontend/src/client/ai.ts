// AI 应用接口客户端（设计文档 v2：问答 / Prompt / Agent 工具 / RAG 评估）。
// 与模板自带的 axios 客户端共存：普通 JSON 接口走 __request，SSE 流式问答走 fetch+ReadableStream。
import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request as __request } from "./core/request"

// ---------------------------------------------------------------------------
// 类型定义（对齐后端 schemas / models）
// ---------------------------------------------------------------------------
export type ChatSessionPublic = {
  id: string
  title?: string | null
  user_id: string
  knowledge_base_id: string
  created_at?: string | null
  updated_at?: string | null
}

export type ChatSessionsPublic = {
  data: Array<ChatSessionPublic>
  count: number
}

export type ChatSessionCreate = {
  title?: string | null
  knowledge_base_id: string
}

export type ChatMessagePublic = {
  id: string
  role: string
  content: string
  session_id: string
  model_name?: string | null
  prompt_version?: string | null
  created_at?: string | null
}

export type ChatMessagesPublic = {
  data: Array<ChatMessagePublic>
  count: number
}

export type Citation = {
  chunk_id: string
  document: string
  chunk_index: number
  preview: string
}

export type Usage = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens?: number
  estimated_cost: number
}

export type AskRequest = {
  query: string
  top_k?: number
  use_agent?: boolean
  prompt_version?: string | null
}

export type AskResponse = {
  answer: string
  is_refused: boolean
  citations: Array<Citation>
  retrieval_count: number
  usage: Usage
  trace_id?: string | null
}

export type PromptConfigPublic = {
  id: string
  name: string
  version: string
  system_prompt: string
  retrieval_template?: string | null
  answer_template?: string | null
  is_active: boolean
  created_at?: string | null
}

export type PromptConfigsPublic = {
  data: Array<PromptConfigPublic>
  count: number
}

export type PromptConfigCreate = {
  name: string
  version: string
  system_prompt: string
  retrieval_template?: string | null
  answer_template?: string | null
  is_active?: boolean
}

export type ToolSchema = {
  type?: string
  function?: {
    name: string
    description?: string
    parameters?: Record<string, unknown>
  }
  // 兼容扁平结构
  name?: string
  description?: string
}

export type ToolsListResponse = { tools: Array<ToolSchema> }
export type ToolRunResponse = { result: unknown }

export type EvalRunRequest = {
  dataset?: Array<Record<string, unknown>> | null
  top_k?: number | null
  prompt_version?: string | null
  with_generation?: boolean
}

export type EvalRunResponse = {
  config: Record<string, unknown>
  num_items: number
  hit_rate_at_k: number
  mrr: number
  recall_at_k: number
  context_precision: number
  avg_relevance: number
  faithfulness: number
  avg_latency_ms: number
  estimated_cost: number
  refusal_rate: number
}

export type DocumentCreate = {
  file_name: string
  file_type: string
  content_type?: string | null
  file_size?: number
  processing_status?: string
  chunk_strategy?: string
  summary?: string | null
  knowledge_base_id: string
}

// ---------------------------------------------------------------------------
// 会话与问答（/chat）
// ---------------------------------------------------------------------------
export class ChatService {
  public static listSessions(
    data: { skip?: number; limit?: number } = {},
  ): CancelablePromise<ChatSessionsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/chat/sessions",
      query: { skip: data.skip, limit: data.limit },
      errors: { 422: "Validation Error" },
    })
  }

  public static createSession(data: {
    requestBody: ChatSessionCreate
  }): CancelablePromise<ChatSessionPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/chat/sessions",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 404: "Not Found", 422: "Validation Error" },
    })
  }

  public static listMessages(data: {
    id: string
  }): CancelablePromise<ChatMessagesPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/chat/sessions/{id}/messages",
      path: { id: data.id },
      errors: { 404: "Not Found", 422: "Validation Error" },
    })
  }

  public static ask(data: {
    id: string
    requestBody: AskRequest
  }): CancelablePromise<AskResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/chat/sessions/{id}/ask",
      path: { id: data.id },
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 403: "Forbidden", 404: "Not Found", 429: "Rate Limited", 422: "Validation Error" },
    })
  }
}

// SSE 事件统一形状（设计文档 7.3：retrieval -> token* -> done）。
export type StreamEvent =
  | { event: "retrieval"; data: { citations?: Citation[]; retrieval_count?: number; is_refused?: boolean } }
  | { event: "token"; data: { token?: string; text?: string; content?: string } }
  | { event: "done"; data: { usage?: Usage; trace_id?: string | null; is_refused?: boolean } }
  | { event: "error"; data: { detail?: string } }
  | { event: string; data: Record<string, unknown> }

// 流式问答：fetch + ReadableStream 手动解析 SSE 帧，逐 token 回调。
// 模板自带的 axios 客户端不支持流式读取，故此处单独实现，复用同一 token 来源。
export async function askStream(
  sessionId: string,
  body: AskRequest,
  handlers: {
    onEvent: (evt: StreamEvent) => void
    signal?: AbortSignal
  },
): Promise<void> {
  const token =
    typeof OpenAPI.TOKEN === "function"
      ? await OpenAPI.TOKEN({} as never)
      : OpenAPI.TOKEN || localStorage.getItem("access_token") || ""

  const resp = await fetch(
    `${OpenAPI.BASE}/api/v1/chat/sessions/${sessionId}/ask/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
      signal: handlers.signal,
    },
  )

  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      detail = (j as { detail?: string }).detail || detail
    } catch {
      /* ignore */
    }
    handlers.onEvent({ event: "error", data: { detail } })
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 帧以空行分隔
    let sep: number
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let event = "message"
      const dataLines: string[] = []
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim()
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length === 0) continue
      try {
        const data = JSON.parse(dataLines.join("\n"))
        handlers.onEvent({ event, data } as StreamEvent)
      } catch {
        handlers.onEvent({ event, data: { raw: dataLines.join("\n") } })
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Prompt 配置（/prompts）
// ---------------------------------------------------------------------------
export class PromptsService {
  public static listPrompts(
    data: { skip?: number; limit?: number } = {},
  ): CancelablePromise<PromptConfigsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/prompts/",
      query: { skip: data.skip, limit: data.limit },
      errors: { 422: "Validation Error" },
    })
  }

  public static createPrompt(data: {
    requestBody: PromptConfigCreate
  }): CancelablePromise<PromptConfigPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/prompts/",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  }

  public static updatePrompt(data: {
    id: string
    requestBody: PromptConfigCreate
  }): CancelablePromise<PromptConfigPublic> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/prompts/{id}",
      path: { id: data.id },
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 404: "Not Found", 422: "Validation Error" },
    })
  }

  public static activatePrompt(data: {
    id: string
  }): CancelablePromise<PromptConfigPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/prompts/{id}/activate",
      path: { id: data.id },
      errors: { 404: "Not Found", 422: "Validation Error" },
    })
  }
}

// ---------------------------------------------------------------------------
// Agent 工具（/tools）
// ---------------------------------------------------------------------------
export class ToolsService {
  public static listTools(): CancelablePromise<ToolsListResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/tools/",
    })
  }

  public static runTool(data: {
    requestBody: { tool_name: string; args: Record<string, unknown> }
  }): CancelablePromise<ToolRunResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/tools/run",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 404: "Not Found", 500: "Tool Error", 422: "Validation Error" },
    })
  }
}

// ---------------------------------------------------------------------------
// RAG 评估（/eval）
// ---------------------------------------------------------------------------
export class EvalService {
  public static runEval(data: {
    requestBody: EvalRunRequest
  }): CancelablePromise<EvalRunResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/eval/run",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  }
}

// 文档创建（模板已有 read，补充 create —— 用于演示上传/登记文档）
export class DocumentsWriteService {
  public static createDocument(data: {
    requestBody: DocumentCreate
  }): CancelablePromise<unknown> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/documents/",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 403: "Forbidden", 404: "Not Found", 422: "Validation Error" },
    })
  }

  public static deleteDocument(data: {
    id: string
  }): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/documents/{id}",
      path: { id: data.id },
      errors: { 403: "Forbidden", 404: "Not Found", 422: "Validation Error" },
    })
  }
}
