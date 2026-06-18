"""LLM 服务（设计文档 8.2 步骤 7、8.4 多模型容灾）。

抽象 LLM Provider，把「生成能力」与供应商解耦：
- mock:   无需 API Key，回显确定性答案并标注引用，供 smoke test；
- openai: AsyncOpenAI chat.completions（支持流式）；
- claude: Anthropic Messages API（支持流式）。

统一能力：
- chat(messages, model?, stream=False)         非流式，返回 {content, usage, model}
- stream_chat(messages, model?)                异步生成器，逐 token 产出文本
- count_tokens(text)                           tiktoken 优先，降级 len//4
- estimate_cost(model, prompt_tokens, completion_tokens)

主 Provider 异常时按 settings.LLM_FALLBACK_MODEL 容灾（设计文档 8.4）。
"""

from __future__ import annotations

from typing import AsyncIterator

from app.core.config import settings

# 简单的每千 token 价格表（美元），仅用于成本估算与成本路由度量（设计文档 18.2）
_PRICE_PER_1K = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.005, 0.015),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-haiku": (0.00025, 0.00125),
}
_DEFAULT_PRICE = (0.0005, 0.0015)


class LLMService:
    """LLM Provider 抽象 + 统一入口 + fallback。"""

    def __init__(
        self, provider: str | None = None, model: str | None = None
    ) -> None:
        self.provider = (provider or settings.LLM_PROVIDER or "mock").lower()
        self.model = model or settings.LLM_MODEL
        self.fallback_model = settings.LLM_FALLBACK_MODEL
        self._openai = None
        self._anthropic = None

    # ----------------------------------------------------------- tokenizer
    def count_tokens(self, text: str) -> int:
        """估算 token 数：tiktoken 优先，不可用时按 4 字符/token 近似。"""
        if not text:
            return 0
        try:
            import tiktoken

            try:
                enc = tiktoken.encoding_for_model(self.model)
            except Exception:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def estimate_cost(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """按价格表估算调用成本（美元）。"""
        in_price, out_price = _PRICE_PER_1K.get(model, _DEFAULT_PRICE)
        return round(
            (prompt_tokens / 1000) * in_price
            + (completion_tokens / 1000) * out_price,
            6,
        )

    def _usage(
        self, model: str, prompt_text: str, completion_text: str
    ) -> dict:
        pt = self.count_tokens(prompt_text)
        ct = self.count_tokens(completion_text)
        return {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "estimated_cost": self.estimate_cost(model, pt, ct),
            "model": model,
        }

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        return "\n".join(m.get("content", "") for m in messages)

    # ---------------------------------------------------------------- mock
    def _mock_answer(self, messages: list[dict]) -> str:
        """Mock 模式：从检索片段中找到与问题最相关的 Q&A 对返回。"""
        import re

        user_msgs = [m for m in messages if m.get("role") == "user"]
        last = user_msgs[-1]["content"] if user_msgs else ""

        lines = last.strip().split("\n")
        question = ""
        chunk_texts: list[str] = []
        in_chunk = False

        for line in lines:
            stripped = line.strip()
            if "用户问题" in stripped or "Question:" in stripped:
                question = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
                in_chunk = False
                continue
            if any(kw in stripped for kw in [
                "检索到的相关片段", "请依据", "回答要求", "标注引用", "---",
                "检索上下文", "请根据"
            ]):
                in_chunk = False
                continue
            if stripped.startswith("[") and "]" in stripped:
                in_chunk = True
                text_after = stripped.split("]", 1)[-1].strip()
                if text_after and not text_after.startswith("(来源"):
                    chunk_texts.append(text_after)
                continue
            if stripped.startswith("(来源"):
                continue
            if in_chunk and stripped:
                chunk_texts.append(stripped)

        if not question:
            question = lines[-1].strip()[:100] if lines else "未知问题"

        # 把所有 chunk 文本合并，按 Q&A 对拆分，找最匹配的答案
        full_text = "\n".join(chunk_texts)
        # 匹配 "Q：..." 后跟 "A：..." 的模式
        qa_pairs = re.split(r'(?=\d+\.\s*Q[：:])', full_text)
        # 用问题关键词匹配最佳 QA 对
        q_keywords = [w for w in re.split(r'[，。？！/、\s]+', question) if len(w) >= 2]

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
            # 提取 A：后面的内容作为答案
            a_match = re.search(r'A[：:]\s*(.+)', best_pair, re.DOTALL)
            if a_match:
                answer_text = a_match.group(1).strip()
                answer_text = re.sub(r'\s*\d+\.\s*Q[：:].*', '', answer_text, flags=re.DOTALL)
                return answer_text[:600]
            return best_pair[:600]

        # 没有匹配到 QA 对，返回包含关键词最多的文本段
        if chunk_texts:
            best_line = max(chunk_texts, key=lambda t: sum(1 for kw in q_keywords if kw in t))
            return f"关于「{question}」：{best_line[:500]}"
        return f"暂未在知识库中找到与「{question}」直接相关的内容，建议补充相关文档后重试。"

    # ----------------------------------------------------- openai / claude
    def _get_openai(self):
        if self._openai is None:
            from openai import AsyncOpenAI

            self._openai = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        return self._openai

    def _get_anthropic(self):
        if self._anthropic is None:
            from anthropic import AsyncAnthropic

            self._anthropic = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._anthropic

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        """Anthropic 需要把 system 抽出为单独参数。"""
        system = "\n".join(
            m["content"] for m in messages if m.get("role") == "system"
        )
        convo = [m for m in messages if m.get("role") != "system"]
        return system, convo

    async def _openai_chat(self, messages: list[dict], model: str) -> str:
        client = self._get_openai()
        resp = await client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content or ""

    async def _claude_chat(self, messages: list[dict], model: str) -> str:
        client = self._get_anthropic()
        system, convo = self._split_system(messages)
        resp = await client.messages.create(
            model=model,
            system=system or None,
            messages=convo,
            max_tokens=1024,
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts)

    # ---------------------------------------------------------------- chat
    async def chat(
        self, messages: list[dict], model: str | None = None, stream: bool = False
    ) -> dict:
        """非流式生成。stream=True 时聚合流式输出为完整文本后返回。

        返回 {content, usage}。主 Provider 异常时回退 fallback 模型。
        """
        model = model or self.model
        prompt_text = self._flatten(messages)

        if stream:
            chunks = [tok async for tok in self.stream_chat(messages, model)]
            content = "".join(chunks)
            return {"content": content, "usage": self._usage(model, prompt_text, content)}

        try:
            if self.provider == "openai":
                content = await self._openai_chat(messages, model)
            elif self.provider == "claude":
                content = await self._claude_chat(messages, model)
            else:
                content = self._mock_answer(messages)
        except Exception:
            # 8.4 多模型容灾：主链路异常 -> fallback 模型，再失败则降级 mock
            if self.fallback_model and self.provider != "mock":
                try:
                    if self.provider == "openai":
                        content = await self._openai_chat(messages, self.fallback_model)
                    else:
                        content = await self._claude_chat(messages, self.fallback_model)
                    model = self.fallback_model
                except Exception:
                    content = self._mock_answer(messages)
            else:
                content = self._mock_answer(messages)

        return {"content": content, "usage": self._usage(model, prompt_text, content)}

    async def stream_chat(
        self, messages: list[dict], model: str | None = None
    ) -> AsyncIterator[str]:
        """流式生成：逐 token（或文本片段）产出。

        mock / 异常降级时按词切分本地答案，保证 SSE 链路可离线验证。
        """
        model = model or self.model
        try:
            if self.provider == "openai":
                client = self._get_openai()
                stream = await client.chat.completions.create(
                    model=model, messages=messages, stream=True
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                return
            if self.provider == "claude":
                client = self._get_anthropic()
                system, convo = self._split_system(messages)
                async with client.messages.stream(
                    model=model, system=system or None, messages=convo, max_tokens=1024
                ) as stream:
                    async for text_piece in stream.text_stream:
                        yield text_piece
                return
        except Exception:
            # 降级到本地伪流式
            pass

        # mock / 降级：按空白切词产出
        answer = self._mock_answer(messages)
        for token in answer.split(" "):
            yield token + " "
