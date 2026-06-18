"""文本切分策略（设计文档 8.1 步骤 3 / models.Document.chunk_strategy）。

提供三种纯函数切分策略，均返回 list[str]（已去除空块）：
- fixed:     固定字符窗口 + overlap，适合无结构纯文本，保证片段长度均衡。
- markdown:  按 Markdown 标题（# 行）切分，保留章节语义边界。
- paragraph: 按空行分段，贴近自然语义单元。

设计为纯函数（无 IO、无副作用），便于单测与在 worker / service 中复用。
"""

from __future__ import annotations

import re


def chunk_fixed(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """固定窗口切分：每片 size 字符，相邻片重叠 overlap 字符。

    overlap 用于缓解「关键信息恰好落在切分边界」导致的语义割裂。
    """
    text = (text or "").strip()
    if not text:
        return []
    if size <= 0:
        return [text]
    if overlap < 0:
        overlap = 0
    if overlap >= size:
        # 防止步长非正导致死循环
        overlap = size - 1

    step = size - overlap
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        start += step
    return chunks


def chunk_markdown(text: str) -> list[str]:
    """按 Markdown 标题切分：每个 # 标题开启一个新片段，保留章节结构。

    标题行（^#{1,6}\\s）作为分隔锚点，标题与其下正文归入同一片段。
    无标题时退化为整段返回。
    """
    text = (text or "").strip()
    if not text:
        return []

    heading_re = re.compile(r"^#{1,6}\s")
    sections: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if heading_re.match(line):
            # 遇到新标题，结束上一节
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    chunks = ["\n".join(sec).strip() for sec in sections]
    return [c for c in chunks if c]


def chunk_paragraph(text: str) -> list[str]:
    """按段落切分：以一个或多个空行作为段落分隔。

    针对 QA 格式文档自动检测：若文本含 "Q：" 问答对格式，
    按 "数字. Q：" 分割（每个 chunk 是一个完整 QA 对）。
    """
    text = (text or "").strip()
    if not text:
        return []
    # 检测 QA 格式：若包含多个 "数字. Q：" 或 "Q：" 模式，按 QA 对分割
    qa_markers = re.findall(r"\d+\.\s*Q[：:]", text)
    if len(qa_markers) >= 3:
        parts = re.split(r"(?=\d+\.\s*Q[：:])", text)
        chunks = [p.strip() for p in parts if p.strip() and re.search(r"Q[：:]", p)]
        if chunks:
            return chunks
    # 普通段落切分
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def dispatch(strategy: str, text: str) -> list[str]:
    """按策略名分发到对应切分函数（默认 fixed）。

    对应 Document.chunk_strategy 的取值：fixed / markdown / paragraph。
    """
    strategy = (strategy or "fixed").lower()
    if strategy == "markdown":
        return chunk_markdown(text)
    if strategy == "paragraph":
        return chunk_paragraph(text)
    return chunk_fixed(text)
