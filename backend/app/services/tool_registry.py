"""ToolRegistry：工具注册中心（设计文档 4.8）。

集中注册本地工具与 MCP 工具，向 AgentService 提供：
- register(tool)：注册工具实例；
- get(name)：按名取工具；
- list_schemas()：导出全部工具的 OpenAI function-calling JSON Schema 列表，
  直接作为 LLMService.chat(tools=...) 的入参。

get_registry() 提供单例式访问，首次调用时注册内置工具（kb 信息 / 文档元数据 /
知识库检索）。MCP 工具因依赖外部 server 配置，默认不自动注册，由上层按需 register。
"""

from __future__ import annotations

from typing import Any

from app.services.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具；同名工具后注册覆盖前者。"""
        if not getattr(tool, "name", None):
            raise ValueError("工具必须有非空 name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def list_schemas(self) -> list[dict[str, Any]]:
        """导出全部工具的 function-calling schema 列表。"""
        return [tool.to_schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())


# 模块级单例
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """返回全局 ToolRegistry 单例，首次调用注册内置工具。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_builtin_tools(_registry)
    return _registry


def _register_builtin_tools(registry: ToolRegistry) -> None:
    """注册内置本地工具。容错导入，单个工具失败不影响其余注册。"""
    from app.services.tools.document_meta_tool import DocumentMetaTool
    from app.services.tools.kb_info_tool import KbInfoTool
    from app.services.tools.kb_search_tool import KbSearchTool

    for tool_cls in (KbSearchTool, KbInfoTool, DocumentMetaTool):
        try:
            registry.register(tool_cls())
        except Exception:  # noqa: BLE001 - 单工具注册失败不应中断整体
            continue
