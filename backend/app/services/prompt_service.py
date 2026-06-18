"""Prompt 配置服务（设计文档 4.6 / 6.8 / 8.2 步骤 6）。

提供可版本化、可热切换的 Prompt 管理（同 name 仅一个 active），并负责
把 active 配置 + 检索片段 + 历史 + query 渲染为 LLM messages。

强约束反幻觉系统提示词：仅依据检索片段作答，无依据则明确说不知道，并标注引用 [n]。
"""

from __future__ import annotations

import uuid

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import PromptConfig, PromptConfigCreate

# 反幻觉系统提示词（设计文档 4.4 拒答 / 4.6 prompt 工程）
_DEFAULT_SYSTEM_PROMPT = (
    "你是严谨的企业知识库问答助手。请严格遵守以下规则：\n"
    "1. 仅依据下方提供的「检索片段」作答，不得使用片段之外的知识或自行推测。\n"
    "2. 若检索片段中没有足够依据，必须明确回答「根据现有资料，我无法回答这个问题」，不要编造。\n"
    "3. 引用片段内容时，在句末用 [n] 标注对应片段编号，便于溯源。\n"
    "4. 回答使用与用户提问一致的语言，简洁、准确。"
)

_DEFAULT_RETRIEVAL_TEMPLATE = "[{index}] (来源：{document}#{chunk_index})\n{content}"

_DEFAULT_ANSWER_TEMPLATE = (
    "以下是检索到的相关片段：\n\n{context}\n\n"
    "用户问题：{query}\n\n"
    "请依据上述片段作答，并标注引用编号。"
)


class PromptService:
    """围绕单个 AsyncSession 的 Prompt 配置管理与渲染。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, name: str = "default") -> PromptConfig | None:
        """获取指定 name 下当前 active 的 Prompt 配置。"""
        stmt = select(PromptConfig).where(
            PromptConfig.name == name, PromptConfig.is_active == True  # noqa: E712
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def get_by_version(
        self, name: str, version: str
    ) -> PromptConfig | None:
        stmt = select(PromptConfig).where(
            PromptConfig.name == name, PromptConfig.version == version
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def create(self, prompt_in: PromptConfigCreate) -> PromptConfig:
        db_obj = PromptConfig.model_validate(prompt_in)
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def activate(self, prompt_id: uuid.UUID) -> PromptConfig | None:
        """激活指定配置，并把同 name 下其他配置全部置为非 active。"""
        target = await self.session.get(PromptConfig, prompt_id)
        if not target:
            return None
        stmt = select(PromptConfig).where(PromptConfig.name == target.name)
        result = await self.session.exec(stmt)
        for cfg in result.all():
            cfg.is_active = cfg.id == prompt_id
            self.session.add(cfg)
        await self.session.commit()
        await self.session.refresh(target)
        return target

    async def seed_default(self) -> PromptConfig:
        """植入默认 active Prompt（幂等：已存在则直接返回）。"""
        existing = await self.get_active("default")
        if existing:
            return existing
        prompt_in = PromptConfigCreate(
            name="default",
            version="v1",
            system_prompt=_DEFAULT_SYSTEM_PROMPT,
            retrieval_template=_DEFAULT_RETRIEVAL_TEMPLATE,
            answer_template=_DEFAULT_ANSWER_TEMPLATE,
            is_active=True,
        )
        return await self.create(prompt_in)

    def render(
        self,
        prompt_config: PromptConfig | None,
        context_chunks: list[dict],
        history: list[dict],
        query: str,
    ) -> list[dict]:
        """渲染为 LLM messages：system -> 历史 -> 带上下文的当前 user 消息。

        context_chunks: 检索结果 dict 列表（含 content/document/chunk_index）。
        history:        既往消息 [{role, content}]，按时间升序。
        """
        system_prompt = (
            prompt_config.system_prompt if prompt_config else _DEFAULT_SYSTEM_PROMPT
        )
        retrieval_tmpl = (
            (prompt_config.retrieval_template if prompt_config else None)
            or _DEFAULT_RETRIEVAL_TEMPLATE
        )
        answer_tmpl = (
            (prompt_config.answer_template if prompt_config else None)
            or _DEFAULT_ANSWER_TEMPLATE
        )

        # 拼接带编号的检索上下文，编号与 citations [n] 对齐（从 1 开始）
        rendered_chunks = []
        for i, c in enumerate(context_chunks, start=1):
            rendered_chunks.append(
                retrieval_tmpl.format(
                    index=i,
                    document=c.get("document", ""),
                    chunk_index=c.get("chunk_index", ""),
                    content=c.get("content", ""),
                )
            )
        context = "\n\n".join(rendered_chunks) if rendered_chunks else "（无检索片段）"

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append(
            {
                "role": "user",
                "content": answer_tmpl.format(context=context, query=query),
            }
        )
        return messages
