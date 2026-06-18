"""McpTool：外部 MCP server 接入（设计文档 4.8 "MCP 接入"）。

MCP（Model Context Protocol）允许把外部工具服务器（filesystem / fetch / git 等）
的工具以统一 schema 暴露给模型。本模块的设计目标：

1. 若安装了官方 `mcp` python 包：通过 stdio / HTTP 连接 MCP server，
   列出其工具并按需调用，结果归一化为本项目的 BaseTool 契约。
2. 若未安装 `mcp` 包（CI / 离线环境）：提供清晰注释的桩实现，
   返回 "MCP not configured" 结果，并保证本文件在无 mcp 包时可 py_compile。

接入方式（stdio 为例，注释说明真实接线）：
    server_params = StdioServerParameters(command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/data"])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            tools = await sess.list_tools()          # 列出远端工具
            result = await sess.call_tool(name, args) # 调用远端工具
"""

from __future__ import annotations

from typing import Any

from app.services.tools.base import BaseTool

# 可选依赖：mcp 包。导入失败不影响 py_compile 与其余功能。
try:  # pragma: no cover - 取决于运行环境是否安装 mcp
    import mcp  # type: ignore  # noqa: F401
    from mcp import ClientSession  # type: ignore  # noqa: F401
    from mcp.client.stdio import StdioServerParameters, stdio_client  # type: ignore  # noqa: F401

    _MCP_AVAILABLE = True
except Exception:  # noqa: BLE001
    _MCP_AVAILABLE = False


class McpTool(BaseTool):
    """把单个 MCP server（及其某个远端工具）封装为本项目工具。

    :param server_command: 启动 MCP server 的命令（stdio 模式），如 "npx"。
    :param server_args: 命令参数列表。
    :param remote_tool_name: 要暴露的远端工具名；为空时 run() 走 list_tools 探测。
    :param name/description: 暴露给模型的统一名称与描述。
    """

    def __init__(
        self,
        server_command: str | None = None,
        server_args: list[str] | None = None,
        remote_tool_name: str | None = None,
        name: str = "mcp_tool",
        description: str | None = None,
        transport: str = "stdio",
    ) -> None:
        self.server_command = server_command
        self.server_args = server_args or []
        self.remote_tool_name = remote_tool_name
        self.transport = transport
        self.name = name
        self.description = description or (
            "通过 MCP 协议调用外部工具服务器（如 filesystem / fetch）。"
            "用于访问知识库之外的外部能力。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        # MCP 远端工具参数各异，这里用通用 object 透传；真实接入可在
        # 初始化时拉取远端 inputSchema 动态替换为精确 schema。
        return {
            "type": "object",
            "properties": {
                "args": {
                    "type": "object",
                    "description": "透传给 MCP 远端工具的参数对象",
                }
            },
            "required": [],
        }

    async def run(self, args: dict[str, Any], session: Any | None = None) -> Any:
        remote_args = args.get("args", args) or {}
        if not _MCP_AVAILABLE:
            # 桩路径：未安装 mcp 包。明确告知调用方未配置，便于排障。
            return {
                "status": "MCP not configured",
                "reason": "python 包 `mcp` 未安装；请 `pip install mcp` 并配置 server_command",
                "would_call": {
                    "command": self.server_command,
                    "args": self.server_args,
                    "tool": self.remote_tool_name,
                    "tool_args": remote_args,
                },
            }
        if not self.server_command:
            return {"status": "MCP not configured", "reason": "未提供 server_command"}

        # 真实路径：建立 stdio 连接 → 初始化 → 调用远端工具。
        try:  # pragma: no cover - 需要真实 MCP server 才会执行
            params = StdioServerParameters(  # type: ignore[name-defined]
                command=self.server_command, args=self.server_args
            )
            async with stdio_client(params) as (read, write):  # type: ignore[name-defined]
                async with ClientSession(read, write) as mcp_session:  # type: ignore[name-defined]
                    await mcp_session.initialize()
                    if not self.remote_tool_name:
                        listed = await mcp_session.list_tools()
                        return {"available_tools": _safe_serialize(listed)}
                    call_result = await mcp_session.call_tool(
                        self.remote_tool_name, remote_args
                    )
                    return {"result": _safe_serialize(call_result)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "MCP call failed", "error": str(exc)}

    async def list_remote_tools(self) -> Any:
        """探测远端工具列表（真实环境用；桩环境返回未配置）。"""
        if not _MCP_AVAILABLE or not self.server_command:
            return {"status": "MCP not configured"}
        return await self.run({"args": {}})  # remote_tool_name 为空时即列出工具


def _safe_serialize(obj: Any) -> Any:
    """把 MCP SDK 的返回对象尽量转为可 JSON 序列化结构。"""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "__dict__"):
        return {k: str(v) for k, v in vars(obj).items()}
    return str(obj)
