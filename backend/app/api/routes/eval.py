"""RAG 评估接口（设计文档 7.6 / 18.3：POST /eval/run）。

输入可选标注集，跑评估并返回核心指标（Hit@k、MRR、答案相关性等）。
鉴权必填（设计文档 11.1）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.services.eval_service import EvalService, ExperimentSnapshot

router = APIRouter(prefix="/eval", tags=["eval"])


class EvalRunRequest(BaseModel):
    # 可选：直接传标注集；为空则用内置 sample_golden.json
    dataset: list[dict[str, Any]] | None = None
    # 可选：实验配置覆盖
    top_k: int | None = None
    prompt_version: str | None = None
    with_generation: bool = True


class EvalRunResponse(BaseModel):
    config: dict[str, Any]
    num_items: int
    hit_rate_at_k: float
    mrr: float
    recall_at_k: float
    context_precision: float
    avg_relevance: float
    faithfulness: float
    avg_latency_ms: float
    estimated_cost: float
    refusal_rate: float


@router.post("/run", response_model=EvalRunResponse)
async def run_eval(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    body: EvalRunRequest,
) -> EvalRunResponse:
    """跑一次 RAG 评估，返回聚合指标。"""
    config = ExperimentSnapshot()
    if body.top_k is not None:
        config.top_k = body.top_k
    if body.prompt_version is not None:
        config.prompt_version = body.prompt_version

    service = EvalService(session=session)
    result = await service.run_experiment(
        dataset=body.dataset,
        config=config,
        with_generation=body.with_generation,
    )
    return EvalRunResponse(
        config=result.config,
        num_items=result.num_items,
        hit_rate_at_k=result.hit_at_k,
        mrr=result.mrr,
        recall_at_k=result.recall_at_k,
        context_precision=result.context_precision,
        avg_relevance=result.answer_relevancy,
        faithfulness=result.faithfulness,
        avg_latency_ms=result.avg_latency_ms,
        estimated_cost=result.estimated_cost,
        refusal_rate=result.refusal_rate,
    )
