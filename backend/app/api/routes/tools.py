"""工具直调接口（设计文档 7.5：POST /tools/run）。

提供"直接调用单个工具"的能力（不经 Agent 循环），便于调试工具与前端按需取数。
鉴权必填（设计文档 11.1：默认接口需鉴权，不创建无鉴权对外服务）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.services.tool_registry import get_registry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolRunRequest(BaseModel):
    tool_name: str
    args: dict[str, Any] = {}


class ToolRunResponse(BaseModel):
    result: Any


@router.get("/")
async def list_tools(current_user: CurrentUser) -> dict[str, Any]:
    """列出已注册工具的 function-calling schema（需鉴权）。"""
    registry = get_registry()
    return {"tools": registry.list_schemas()}


@router.post("/run", response_model=ToolRunResponse)
async def run_tool(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    body: ToolRunRequest,
) -> ToolRunResponse:
    """直接调用单个工具。

    校验工具存在；执行异常包装为 500，避免泄漏内部堆栈。
    """
    registry = get_registry()
    tool = registry.get(body.tool_name)
    if tool is None:
        raise HTTPException(
            status_code=404,
            detail=f"工具不存在: {body.tool_name}。可用: {registry.names()}",
        )
    try:
        result = await tool.run(body.args, session=session)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"工具执行失败: {exc}")
    return ToolRunResponse(result=result)
