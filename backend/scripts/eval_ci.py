"""eval_ci.py：CI 友好的 RAG 评估门禁脚本（设计文档 18.3 关键设计 3）。

加载样例黄金集 → 跑评估 → 打印指标 → 核心指标跌破基线则 exit(1) 阻断合并。
在 CI 中把它作为流水线一环：改检索/Prompt 的 PR 自动跑回归。

可直接运行：
    python -m scripts.eval_ci
    python backend/scripts/eval_ci.py
无需 API key 与数据库（EvalService 内置 mock 路径）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许以脚本方式直接运行时找到 app 包
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.eval_service import EvalService, ExperimentSnapshot  # noqa: E402

# 基线阈值（设计文档 18.3：跌破基线则阻断合并）
BASELINE = {
    "hit_at_k": 0.8,
    "mrr": 0.5,
    "answer_relevancy": 0.0,  # mock judge 偏保守，relevancy 阈值设宽松
    "context_precision": 0.0,
}


async def _main() -> int:
    service = EvalService(session=None)  # CI 用 mock 路径
    dataset = service.load_dataset()
    config = ExperimentSnapshot(note="ci-gate")
    result = await service.run_experiment(dataset=dataset, config=config)

    print("=" * 56)  # noqa: T201
    print("RAG 评估结果（CI 门禁）")  # noqa: T201
    print("=" * 56)  # noqa: T201
    print(f"样本数:           {result.num_items}")  # noqa: T201
    print(f"Hit@k:            {result.hit_at_k}")  # noqa: T201
    print(f"MRR:              {result.mrr}")  # noqa: T201
    print(f"Recall@k:         {result.recall_at_k}")  # noqa: T201
    print(f"Context Precision:{result.context_precision}")  # noqa: T201
    print(f"Faithfulness:     {result.faithfulness}")  # noqa: T201
    print(f"Answer Relevancy: {result.answer_relevancy}")  # noqa: T201
    print(f"平均延迟(ms):     {result.avg_latency_ms}")  # noqa: T201
    print(f"估算成本($):      {result.estimated_cost}")  # noqa: T201
    print(f"拒答率:           {result.refusal_rate}")  # noqa: T201
    print("-" * 56)  # noqa: T201

    failures: list[str] = []
    for metric, floor in BASELINE.items():
        value = getattr(result, metric)
        status = "PASS" if value >= floor else "FAIL"
        print(f"[{status}] {metric}: {value} (基线 >= {floor})")  # noqa: T201
        if value < floor:
            failures.append(f"{metric}={value} < {floor}")

    print("=" * 56)  # noqa: T201
    if failures:
        print("评估未通过基线，阻断合并：")  # noqa: T201
        for f in failures:
            print(f"  - {f}")  # noqa: T201
        return 1
    print("评估通过全部基线。")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
