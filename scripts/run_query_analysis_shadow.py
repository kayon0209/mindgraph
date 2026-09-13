"""PR-10｜跑 QueryAnalyzer shadow：现有 90 条集 + 新增 query-understanding 集。

产出三件事：
1. 每条问题的**完整分析输出**（意图/实体/缺槽/复杂度/风险/置信度）；
2. 与生产路由的 **disagreement report**（分歧必须可解释）；
3. 对新增集 ``expected_intent`` 的命中率（校验分析器，不是校验生产）。

⚠️ 本脚本**只读**：不调用生成模型、不改路由、不写数据库。
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter  # noqa: E402
from application.query_analysis import QueryAnalysisService  # noqa: E402

PROJECT_ROOT = _ROOT


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="QueryAnalyzer shadow 跑批")
    parser.add_argument("--golden", default=str(PROJECT_ROOT / "evaluation/datasets/mindgraph_golden_v2.jsonl"))
    parser.add_argument("--understanding", default=str(PROJECT_ROOT / "evaluation/datasets/query_understanding_v1.jsonl"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "evaluation/results"))
    args = parser.parse_args()

    analyzer = QueryAnalysisService()
    router = AdaptiveRetrievalRouter()

    def route_of(question: str) -> str | None:
        """取生产路由。失败返回 None —— 调用方**必须**把 None 记为"无法比较"，
        绝不能当成"一致"（那样分歧数会假性归零）。"""
        try:
            return router.decide(
                question,
                requested_strategy="auto",  # 与 ChatRequest 默认值一致
                graph_allowed=False,
            ).route
        except Exception:
            return None

    sections = {}
    for name, path, _has_expectations in (
        ("golden_v2", Path(args.golden), False),
        ("query_understanding_v1", Path(args.understanding), True),
    ):
        cases = _load(path)
        rows = []
        intent_hits = intent_total = 0
        for case in cases:
            question = str(case.get("question") or "")
            analysis = analyzer.analyze(question)
            row = {
                "case_id": case.get("case_id"),
                "actual_route": route_of(question),
                "analysis": analysis.to_dict(),
            }
            expected = case.get("expected_intent")
            if expected:
                intent_total += 1
                hit = analysis.intent == expected
                intent_hits += int(hit)
                row["expected_intent"] = expected
                row["intent_match"] = hit
            rows.append(row)
        sections[name] = {
            "case_count": len(cases),
            "intent_accuracy": (intent_hits / intent_total) if intent_total else None,
            "rows": rows,
        }

    # 分歧报告：直接复用上面已算好的 expected_route，避免重复加载数据集，
    # 也不把问题原文写进报告（范围外：敏感原文不落日志）。
    qu_rows = sections["query_understanding_v1"]["rows"]
    agreements: list[dict] = []
    disagreements: list[dict] = []
    incomparable: list[dict] = []
    for row in qu_rows:
        analysis = row["analysis"]
        expected, actual = analysis["expected_route"], row["actual_route"]
        entry = {
            "case_id": row["case_id"], "intent": analysis["intent"],
            "expected_route": expected, "actual_route": actual,
            "confidence": analysis["confidence"], "reasons": list(analysis["reasons"]),
        }
        # None 意味着"没有可比对象"，单独计数：把它算进 agreements 会让分歧假性归零
        if actual is None or expected is None:
            entry["reasons"] = [*entry["reasons"],
                                f"incomparable(expected={expected}, actual={actual})"]
            incomparable.append(entry)
        elif expected == actual:
            agreements.append(entry)
        else:
            entry["reasons"] = [*entry["reasons"], f"expected={expected}, actual={actual}"]
            disagreements.append(entry)
    comparable = len(agreements) + len(disagreements)
    disagreement = {
        "case_count": len(qu_rows), "comparable_count": comparable,
        "agreement_count": len(agreements),
        "disagreement_count": len(disagreements),
        "incomparable_count": len(incomparable),
        "agreements": agreements, "disagreements": disagreements,
        "incomparable": incomparable,
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "golden": str(args.golden),
        "understanding": str(args.understanding),
        "sections": sections,
        "disagreement": disagreement,
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"query_analysis_shadow_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, section in sections.items():
        acc = section["intent_accuracy"]
        print(f"{name}: {section['case_count']} 条"
              + (f"｜intent 命中率 {acc:.2%}" if acc is not None else ""))
    print(f"分歧：{disagreement['disagreement_count']} / {disagreement['case_count']}")
    print("\n分歧明细：")
    for item in disagreement["disagreements"][:15]:
        print(f"  {item['case_id']}: expected={item['expected_route']} actual={item['actual_route']}"
              f" | {','.join(item['reasons'][:3])}")
    print(f"\n报告已写入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
