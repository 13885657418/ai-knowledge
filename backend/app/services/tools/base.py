"""Agent 工具抽象基类（设计文档 4.8 / 8.3）。

所有本地工具与 MCP 工具都实现统一的 BaseTool 接口，并通过 `to_schema()`
导出为 OpenAI function-calling 的 JSON Schema，交给模型自主决定是否调用。
这样 AgentService 的 ReAct 循环无需关心工具内部实现，只面向统一契约编排。
"""

from __future__ import annotations

import abc
from typing import Any

# 说明：工具运行时拿到的 session 是 AsyncSession，但这里用 Any 标注以避免
# 在缺少数据库环境时仍能 py_compile（工具可在 mock 路径下不依赖真实 session）。


class BaseTool(abc.ABC):
    """工具抽象基类。

    子类需定义：
    - name：function calling 中的工具名（英文标识符，模型据此调用）；
    - description：自然语言描述，影响模型的工具选择质量；
    - parameters：JSON Schema（OpenAI tools 规范的 parameters 字段）；
    - run()：实际执行逻辑，返回 str 或可 JSON 序列化的 dict。
    """

    #: 工具名（必须是合法的函数名，模型据此发起 tool_call）
    name: str = "base_tool"
    #: 工具描述（决定模型何时选用该工具，需写清用途）
    description: str = "Base tool. Override in subclass."

    @property
    def parameters(self) -> dict[str, Any]:
        """返回 OpenAI function-calling 的 parameters JSON Schema。

        默认空参数；子类按需覆写。
        """
        return {"type": "object", "properties": {}, "required": []}

    @abc.abstractmethod
    async def run(self, args: dict[str, Any], session: Any | None = None) -> Any:
        """执行工具。

        :param args: 模型给出的参数（已按 parameters schema 解析）。
        :param session: 可选 AsyncSession，需要查库的工具使用。
        :return: str 或可 JSON 序列化的 dict（将作为 role=tool 消息回灌模型）。
        """
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        """导出为 OpenAI tools 规范的单个 function schema。

        形如：{"type": "function", "function": {"name", "description", "parameters"}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
