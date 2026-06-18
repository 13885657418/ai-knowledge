# Agent 设计

> 详细说明 ReAct 工具循环、Function Calling 协议、内置工具、MCP 桥接的实现。对应代码：`app/services/agent_service.py`、`tool_registry.py`、`tools/`。

## 1. 总体形态

```
            ┌─────────────────────────────────────────┐
   query ──▶│             AgentService.run            │
            │ ┌─────────────────────────────────────┐ │
            │ │  ReAct Loop  (max AGENT_MAX_STEPS)  │ │
            │ │                                     │ │
            │ │  ┌──────────┐                       │ │
            │ │  │   LLM    │ ─ tool_calls? ─Yes──▶ ToolRegistry.execute(name, args)
            │ │  │  chat    │                                    │ │
            │ │  └────▲─────┘                                    │ │
            │ │       │                                          │ │
            │ │       └─── role=tool message ◀───────────────────┘ │
            │ │            (回灌工具结果)                           │ │
            │ │                                                    │ │
            │ │       ─ tool_calls? ─No ──▶ final answer ─ break  │ │
            │ └────────────────────────────────────────────────────┘ │
            │                                                       │
            └──▶ { answer, steps, citations, usage }                 │
```

- 入口：`POST /chat/sessions/{id}/ask` 加 `"use_agent": true`。
- 退出条件：模型一次响应不再带 `tool_calls`（产出最终答案），或达到 `AGENT_MAX_STEPS`（防失控，默认 5）。

## 2. Function Calling 协议

工具以 OpenAI function-calling schema 暴露：

```json
{
  "type": "function",
  "function": {
    "name": "search_knowledge_base",
    "description": "在指定知识库做混合检索，返回相关切片",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string" },
        "kb_id": { "type": "string" },
        "k":     { "type": "integer", "minimum": 1, "maximum": 10, "default": 4 }
      },
      "required": ["query", "kb_id"]
    }
  }
}
```

`ToolRegistry.list_schemas()` 返回所有工具的 schema 数组，整体作为 `tools` 参数传给 `LLMService.chat()`。

回灌格式：

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "name": "search_knowledge_base",
  "content": "{\"chunks\":[{\"chunk_id\":\"...\",\"document\":\"...\",...}]}"
}
```

## 3. 内置工具

| 名称 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| `search_knowledge_base` | `query, kb_id, k?` | chunks 列表（含 chunk_id / document / preview / score） | 主检索工具，相当于把 RAG 召回作为"工具" |
| `get_document_meta` | `document_id` | 文件名 / 类型 / 大小 / chunk_count / 摘要 | 让模型先看"目录"再决定查哪个文档 |
| `kb_info` | `kb_id` | 知识库统计（文档数 / chunk 总数 / 最近更新时间） | 给模型一个"全景观感" |
| `mcp_tool` | `tool_name, args` | 透传 MCP Server 返回 | 桥接外部工具（详见 §5） |

代码位置：`app/services/tools/`，每个工具继承 `base.Tool`，注册到全局 `ToolRegistry`。

## 4. ReAct 循环细节

```python
class AgentService:
    async def run(self, query: str, kb_id: str) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_AGENT},
            {"role": "user",   "content": query},
        ]
        steps: list[dict] = []
        usage = {...}

        for step in range(settings.AGENT_MAX_STEPS):
            resp = await self.llm.chat(messages, tools=self.registry.list_schemas())
            usage = accumulate(usage, resp.usage)

            if not resp.tool_calls:
                return {
                    "answer": resp.content,
                    "steps": steps,
                    "citations": collected_citations,
                    "usage": usage,
                }

            # 执行所有 tool_calls（并发安全：同一步内是相互独立的）
            messages.append({"role": "assistant", "tool_calls": resp.tool_calls})
            for call in resp.tool_calls:
                result = await self.registry.execute(call.name, call.args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                steps.append({"call": call, "result": result})
                if call.name == "search_knowledge_base":
                    collected_citations.extend(extract_citations(result))

        # 步数耗尽：返回最后一次内容 + 未完成提示
        return {"answer": "[step limit reached] " + last_content, ...}
```

### 步数限制 = 安全阀

- 模型自循环 / 死循环（重复 search 永不收敛）时强制退出。
- 默认 5 步，可按场景调（简单问答 3，复杂多跳 8）。
- 每步把 input / output / latency 写到 `steps[]`，前端可渲染调用链。

## 5. MCP 桥接

> Model Context Protocol（Anthropic 提出）已有大量现成工具（数据库 / Slack / GitHub / 浏览器自动化）。本项目通过一个通用桥接工具 `mcp_tool` 把 MCP Server 的工具集"折叠"进 Agent。

```
LLM ──▶ tool_call: { name="mcp_tool", args={ "tool":"github.search_issues", "params":{...} } }
            │
            ▼
       MCPClient.invoke("github.search_issues", params)
            │
            ▼
       外部 MCP Server (HTTP / stdio)
            │
            ▼
       透传 JSON 结果回灌
```

优势：

- 不用为每个外部能力单独写工具实现，MCP Server 增删工具时 Agent 自动获得。
- 模型可以同时访问私有 KB 和外部世界（如查实时数据、跑 SQL）。

## 6. Agent 与流式

当前实现：Agent 走非流式（`/chat/sessions/{id}/ask`）。

原因：ReAct 需要先看到 `tool_calls` 才能决定执行 / 回灌，首段 token 流出之前必须完成最后一轮工具调用。把 Agent 改造成"工具事件流 + 最终答案流"是后续工作（前端能看到"正在调用 X 工具..."）。

## 7. 与普通 RAG 的关系

```
普通 RAG：           检索 → 生成   （1 次 LLM call）
Agent ReAct：  生成 → [检索/工具 → 生成]*  （N 次 LLM call，每次都可选择是否调工具）
```

何时用 Agent：

- 需要多跳（先查文档目录，再查具体章节）。
- 需要外部数据（结合 MCP Server 拿实时数据）。
- 简单 FAQ 直接走普通 RAG，更快更便宜。

## 8. 防失控清单

| 风险 | 对策 |
| --- | --- |
| 无限循环 | `AGENT_MAX_STEPS` 硬限制 |
| 工具误用 | function schema 严格 `required` + 参数校验 |
| 工具异常 | `ToolRegistry.execute` 捕获异常 → 转成 `{"error": "..."}` 回灌，不阻塞循环 |
| token 爆炸 | 每步累加 usage；超限可在循环里短路返回（设计文档预留） |
| 越权访问 | 每个工具内部强制带 `current_user / kb_id` 归属校验 |
| MCP Server 慢 | `mcp_tool` 内部有超时；工具失败回灌 error，模型可换路 |
