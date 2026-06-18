"""AgentService：function-calling 驱动的 ReAct 工具循环（设计文档 8.3）。

流程：
1. 把 ToolRegistry.list_schemas() 作为 tools 传给 LLMService.chat()；
2. 模型返回 tool_calls → 通过 ToolRegistry 执行对应工具；
3. 工具结果以 role=tool 消息回灌；
4. 循环直至模型产出最终答案（无 tool_calls）或达到 AGENT_MAX_STEPS（防失控，设计文档 15）；
5. 每一步记录到 steps，最终返回 {answer, steps, citations, usage}。

可运行性：LLMService 由另一位同学实现，可能尚不存在。本服务在导入失败或无 API key
时自动切换到内置 _MockLLM——它会先调用一次 search_knowledge_base，再据结果产出答案，
从而让整条 Agent 链路在无 key、无 DB 的环境下也能跑通、可调试。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.services.tool_registry import ToolRegistry, get_registry

# 容错导入真实 LLMService（另一位同学负责）
try:
    from app.services.llm_service import LLMService  # type: ignore

    _LLM_IMPORT_OK = True
except Exception:  # noqa: BLE001
    LLMService = None  # type: ignore
    _LLM_IMPORT_OK = False


class _MockLLM:
    """无 key / LLMService 不可用时的 mock 大模型。

    决策逻辑固定：第一轮要求调用 search_knowledge_base，拿到工具结果后第二轮
    产出最终答案。用于验证 ReAct 循环、工具执行、消息回灌与终止条件。
    """

    def __init__(self) -> None:
        self._called_search = False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        # 是否已经有工具结果回灌（role=tool）
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        user_query = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"), ""
        )
        tool_names = {t["function"]["name"] for t in (tools or [])}

        if not has_tool_result and "search_knowledge_base" in tool_names:
            # 第一步：请求检索
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_mock_1",
                        "type": "function",
                        "function": {
                            "name": "search_knowledge_base",
                            "arguments": json.dumps(
                                {"query": user_query, "kb_id": "mock-kb", "top_k": 4}
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            }
        # 第二步：基于检索结果产出最终答案
        tool_content = ""
        for m in messages:
            if m.get("role") == "tool":
                raw = str(m.get("content", ""))
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict) and "hits" in data:
                        parts = []
                        for hit in data["hits"][:4]:
                            c = hit.get("content", "")
                            if c:
                                parts.append(c)
                        tool_content = "\n".join(parts)
                    else:
                        tool_content = raw[:500]
                except (json.JSONDecodeError, TypeError):
                    tool_content = raw[:500]
        if tool_content:
            # 从检索内容中找与问题最匹配的 QA 对
            import re
            qa_pairs = re.split(r"(?=\d+\.\s*Q[：:])", tool_content)
            q_keywords = [w for w in re.split(r'[，。？！/、\s]+', user_query) if len(w) >= 2]
            best_pair = ""
            best_score = 0
            for pair in qa_pairs:
                if not pair.strip():
                    continue
                score = sum(1 for kw in q_keywords if kw in pair)
                if score > best_score:
                    best_score = score
                    best_pair = pair.strip()
            if best_pair and best_score > 0:
                a_match = re.search(r'A[：:]\s*(.+)', best_pair, re.DOTALL)
                if a_match:
                    answer_text = a_match.group(1).strip()
                    answer_text = re.sub(r'\s*\d+\.\s*Q[：:].*', '', answer_text, flags=re.DOTALL)
                    answer = answer_text[:600]
                else:
                    answer = best_pair[:600]
            else:
                answer = tool_content[:400]
        else:
            answer = f"暂未在知识库中找到与「{user_query}」直接相关的内容。"
        return {
            "role": "assistant",
            "content": answer,
            "tool_calls": None,
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        }


class AgentService:
    def __init__(
        self,
        session: Any | None = None,
        registry: ToolRegistry | None = None,
        llm: Any | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.session = session
        self.registry = registry or get_registry()
        self.max_steps = max_steps or settings.AGENT_MAX_STEPS
        self.llm = llm or self._build_llm()

    def _build_llm(self) -> Any:
        """构造 LLM：优先真实 LLMService，失败或无 key 时退回 _MockLLM。"""
        if _LLM_IMPORT_OK and LLMService is not None:
            has_key = bool(settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY)
            if has_key and settings.LLM_PROVIDER != "mock":
                try:
                    return LLMService()  # type: ignore[call-arg]
                except Exception:  # noqa: BLE001
                    return _MockLLM()
        return _MockLLM()

    async def run(self, query: str, kb_id: str | None = None) -> dict[str, Any]:
        """执行 ReAct 循环，返回最终结果。

        :param query: 用户问题。
        :param kb_id: 知识库 ID，会注入到 system 提示，便于模型填工具参数。
        """
        system_prompt = (
            "你是企业知识库助手。优先使用 search_knowledge_base 工具检索证据后再作答，"
            "仅依据检索到的片段回答，并标注引用。"
        )
        if kb_id:
            system_prompt += f" 当前知识库 ID 为 {kb_id}。"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        tool_schemas = self.registry.list_schemas()

        steps: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        answer = ""

        for step_idx in range(self.max_steps):
            response = await self.llm.chat(messages=messages, tools=tool_schemas)
            self._accumulate_usage(usage_total, response.get("usage"))

            tool_calls = response.get("tool_calls")
            if not tool_calls:
                # 模型给出最终答案，终止循环
                answer = response.get("content") or ""
                steps.append({"step": step_idx, "type": "final", "content": answer})
                break

            # 把 assistant 的 tool_calls 消息加入上下文
            messages.append(
                {
                    "role": "assistant",
                    "content": response.get("content"),
                    "tool_calls": tool_calls,
                }
            )

            # 逐个执行工具调用并回灌结果
            for call in tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                args = self._parse_args(raw_args, kb_id)

                tool = self.registry.get(tool_name)
                if tool is None:
                    tool_result: Any = {"error": f"未知工具: {tool_name}"}
                else:
                    try:
                        tool_result = await tool.run(args, session=self.session)
                    except Exception as exc:  # noqa: BLE001
                        tool_result = {"error": f"工具执行失败: {exc}"}

                # 从检索类工具结果中抽取 citations
                self._collect_citations(tool_name, tool_result, citations)

                steps.append(
                    {
                        "step": step_idx,
                        "type": "tool_call",
                        "tool": tool_name,
                        "args": args,
                        "result": tool_result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": tool_name,
                        "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                    }
                )
        else:
            # 达到最大步数仍未产出答案 —— 防失控收口
            answer = "已达到最大推理步数，未能得出最终答案。"
            steps.append({"step": self.max_steps, "type": "max_steps_reached"})

        return {
            "answer": answer,
            "steps": steps,
            "citations": citations,
            "usage": usage_total,
        }

    @staticmethod
    def _parse_args(raw_args: Any, kb_id: str | None) -> dict[str, Any]:
        """解析模型给出的工具参数；自动补全缺失的 kb_id。"""
        if isinstance(raw_args, dict):
            args = dict(raw_args)
        else:
            try:
                args = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
        # 若模型给的是 mock 占位 kb_id 或缺失，用真实 kb_id 覆盖
        if kb_id and (not args.get("kb_id") or args.get("kb_id") == "mock-kb"):
            args["kb_id"] = kb_id
        return args

    @staticmethod
    def _collect_citations(
        tool_name: str, result: Any, citations: list[dict[str, Any]]
    ) -> None:
        if tool_name != "search_knowledge_base" or not isinstance(result, dict):
            return
        for hit in result.get("hits", []) or []:
            if isinstance(hit, dict):
                citations.append(
                    {
                        "chunk_id": hit.get("chunk_id"),
                        "document_id": hit.get("document_id"),
                        "score": hit.get("score"),
                        "preview": (hit.get("content") or "")[:120],
                    }
                )

    @staticmethod
    def _accumulate_usage(total: dict[str, int], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] += int(usage.get(key, 0) or 0)


# 便于本地手动调试：python -m app.services.agent_service
if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def _demo() -> None:
        agent = AgentService(session=None, llm=_MockLLM())
        out = await agent.run("公司的报销流程是什么？", kb_id="demo-kb")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))  # noqa: T201

    asyncio.run(_demo())
