"""M2 性能基准：单遍 Chat（旧路径）vs 确定性 Assist（新路径）。

方案 M2 验收（非阻断）：在固定本地数据集上记录旧/新路径的 median、P95
与样本数；明显回归需解释并建优化项。本脚本输出机器可读 JSONL 一行。

固定 stub 数据集（无 LLM、无网络）：10 个代表问题 × 每路径 N 次采样，
隔离 provider 延迟（FakeProvider 零成本），度量纯编排开销。

用法：
    .venv/Scripts/python.exe scripts/run_assist_latency_benchmark.py
    .venv/Scripts/python.exe scripts/run_assist_latency_benchmark.py --samples 50 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from application.agent_service import AgentService  # noqa: E402
from application.chat_service import ChatService  # noqa: E402
from domain.models import ChatRequest  # noqa: E402
from infrastructure.database import ProductDatabase  # noqa: E402
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace  # noqa: E402

# 固定问题集：覆盖 M2 ExecutionPolicy 的各路由分支
FIXED_QUESTIONS = [
    ("差旅餐补标准是多少", "factual"),
    ("《费用报销管理办法》的规定", "exact_title"),
    ("报销 v1 和 v2 在 2026-06-01 哪个适用", "structured_fallback"),
    ("报销和招待可以同时吗，对比一下", "cross_policy"),
    ("发票丢了怎么报销，有无例外", "exception_or_conflict"),
    ("国内差旅住宿标准", "factual"),
    ("报销材料清单包括什么", "factual"),
    ("新旧差旅制度的例外情况", "exception_or_conflict"),
    ("招待费和差旅餐补同时发生的规则", "cross_policy"),
    ("v3 住宿标准生效日期", "structured_fallback"),
]


class ZeroCostProvider:
    provider_name = "bench"
    model_name = "bench-model"
    available = True

    def complete(self, _messages):
        return ("依据 [citation-1]。", {"total_tokens": 8})

    def stream(self, _messages):
        yield {"delta": "依据 [citation-1]。"}
        yield {"usage": {"total_tokens": 8}}


class SingleHitPipeline:
    def retrieve(self, *_args, **_kwargs):
        chunk = Chunk(
            "p.md::0", "报销应在 30 日内提交。", "p.md", 0, "时限",
            {"document_title": "费用报销管理办法", "vault_path": "policies/expense.md",
             "document_version": "v2", "effective_from": "2026-01-01", "policy_key": "expense.general",
             "policy_status": "active", "owner": "财务部"},
        )
        return RetrievalTrace(
            query="q", requested_strategy="hybrid", actual_strategy="hybrid",
            candidate_counts={"final": 1},
            final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9)],
            latency_ms={"total_retrieval_ms": 0.2}, index_version="idx", applied_filters={}, warnings=[],
        )


def p_stats(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    samples = max(5, args.samples)

    with tempfile.TemporaryDirectory(prefix="mg-bench-") as raw:
        db = ProductDatabase(Path(raw) / "bench.sqlite3")
        db.initialize()
        chat = ChatService(db, lambda top_k: SingleHitPipeline(), ZeroCostProvider(), privacy_log_questions=False)
        agent = AgentService(chat)
        try:
            legacy_samples: list[float] = []
            assist_samples: list[float] = []
            fallback_events = 0
            for question, _route in FIXED_QUESTIONS:
                request = ChatRequest(question=question, retrieval_strategy="auto", include_retrieval_trace=False)
                for _ in range(samples):
                    started = time.perf_counter()
                    list(chat.stream(request))
                    legacy_samples.append((time.perf_counter() - started) * 1000)

                    started = time.perf_counter()
                    events = list(agent.stream_assist(request))
                    assist_samples.append((time.perf_counter() - started) * 1000)
                    if any(e["event"] == "loop_fell_back" for e in events):
                        fallback_events += 1

            legacy_stats = p_stats(legacy_samples)
            assist_stats = p_stats(assist_samples)
            delta_median = round(assist_stats["median_ms"] - legacy_stats["median_ms"], 3)
            overhead_pct = round((assist_stats["median_ms"] / max(legacy_stats["median_ms"], 0.001) - 1) * 100, 1)
            total_assist_calls = samples * len(FIXED_QUESTIONS)
            fallback_rate = round(fallback_events / total_assist_calls * 100, 2)

            result = {
                "benchmark": "m2_assist_vs_single_pass",
                "dataset": f"fixed-stub-{len(FIXED_QUESTIONS)}-questions",
                "provider_cost": "zero (FakeProvider; orchestration-only)",
                "single_pass": legacy_stats,
                "assist": assist_stats,
                "delta_median_ms": delta_median,
                "overhead_pct_vs_single_pass": overhead_pct,
                "fallback_rate_pct": fallback_rate,
                "note": "非阻断工程基准（方案 M2）：度量确定性 Assist 编排开销（计划/工具轨迹/完整性门），"
                        "不含 LLM 与检索真实耗时；回归解读须以此为前提。",
            }
            print(json.dumps(result, ensure_ascii=False))
            if not args.json:
                print(
                    f"\nsingle-pass median {legacy_stats['median_ms']}ms / p95 {legacy_stats['p95_ms']}ms | "
                    f"assist median {assist_stats['median_ms']}ms / p95 {assist_stats['p95_ms']}ms | "
                    f"编排开销 +{overhead_pct}% | fallback 率 {fallback_rate}%",
                    file=sys.stderr,
                )
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
