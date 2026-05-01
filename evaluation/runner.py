"""评测运行器：逐题调用 RAG 系统，收集答案与检索上下文，混合评分并输出报告。

用法：
    # 在项目根目录运行
    python -m evaluation.runner
    # 或带参数
    python -m evaluation.runner --no-llm-check   # 跳过 LLM 幻觉检测（省 token）
    python -m evaluation.runner --cases 1-10      # 只跑指定题号
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# 设置 UTF-8 编码（Windows 兼容）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 确保 src/ 在路径中
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import ZHIPU_API_KEY, CHAT_MODEL
from rag_engine import ask, build_context
from zhipuai import ZhipuAI

from evaluation.test_cases import ALL_CASES, STANDARD_CASES, ADVERSARIAL_CASES
from evaluation.scorer import score_case, ScoreResult


def run_evaluation(
    *,
    api_key: str,
    cases: Optional[List[Dict]] = None,
    enable_llm_check: bool = True,
) -> Dict:
    """执行全部评测，返回结果字典。"""
    if not api_key:
        print("错误：未配置 ZHIPU_API_KEY，请在 .env 中设置。")
        sys.exit(1)

    client = ZhipuAI(api_key=api_key) if enable_llm_check else None
    test_cases = cases or ALL_CASES

    results: List[ScoreResult] = []
    details: List[Dict] = []

    print(f"\n{'='*60}")
    print(f"评测开始：共 {len(test_cases)} 题")
    print(f"LLM 幻觉检测：{'开启' if enable_llm_check else '关闭'}")
    print(f"{'='*60}\n")

    for i, case in enumerate(test_cases, 1):
        q = case["question"]
        print(f"[{i}/{len(test_cases)}] {case['category']} | {q}")

        try:
            # 获取 RAG 回答与检索上下文
            rag_ans = ask(api_key, q)
            answer = rag_ans.answer

            # 复用 ask() 已经拿到的检索上下文，避免拒答题和评测阶段重复调用 Embedding API。
            context = build_context(rag_ans.sources) if rag_ans.sources else ""
        except Exception as e:
            answer = f"系统错误：{e}"
            context = ""

        # 评分
        sr = score_case(
            case, answer, context,
            client=client,
            model=CHAT_MODEL,
            enable_llm_check=enable_llm_check,
        )
        results.append(sr)

        # 打印单题结果
        status = "PASS" if sr.final_score >= 0.6 else "FAIL"
        print(f"  → [{status}] 综合={sr.final_score:.2f}  关键词={sr.keyword_score:.1f}  幻觉={sr.hallucination_score:.1f}")
        if sr.keyword_hits:
            print(f"    关键词命中：{sr.keyword_hits}")
        if sr.hallucination_reason:
            print(f"    幻觉检测：{sr.hallucination_reason}")
        print()

        # 避免触发速率限制
        time.sleep(0.5)

    # 汇总统计
    total = len(results)
    passed = sum(1 for r in results if r.final_score >= 0.6)
    avg_final = sum(r.final_score for r in results) / total if total else 0
    avg_kw = sum(r.keyword_score for r in results) / total if total else 0
    avg_hal = sum(r.hallucination_score for r in results) / total if total else 0

    # 按类别统计
    by_category: Dict[str, Dict] = {}
    for r in results:
        cat = r.category
        if cat not in by_category:
            by_category[cat] = {"count": 0, "passed": 0, "total_score": 0.0}
        by_category[cat]["count"] += 1
        by_category[cat]["passed"] += 1 if r.final_score >= 0.6 else 0
        by_category[cat]["total_score"] += r.final_score

    # 打印报告
    print(f"\n{'='*60}")
    print("评测报告")
    print(f"{'='*60}")
    print(f"总题数：{total}  通过：{passed}  通过率：{passed/total*100:.1f}%")
    print(f"平均综合分：{avg_final:.2f}")
    print(f"平均关键词分：{avg_kw:.2f}")
    print(f"平均幻觉检测分：{avg_hal:.2f}")
    print()

    print("按类别统计：")
    print(f"  {'类别':<16} {'题数':>4} {'通过':>4} {'通过率':>8} {'平均分':>8}")
    print(f"  {'-'*44}")
    for cat, s in sorted(by_category.items()):
        rate = s["passed"] / s["count"] * 100 if s["count"] else 0
        avg = s["total_score"] / s["count"] if s["count"] else 0
        print(f"  {cat:<16} {s['count']:>4} {s['passed']:>4} {rate:>7.1f}% {avg:>8.2f}")

    # 未通过的题目
    failed = [r for r in results if r.final_score < 0.6]
    if failed:
        print(f"\n未通过题目（{len(failed)}）：")
        for r in failed:
            print(f"  #{r.case_id} [{r.category}] {r.question}")
            print(f"     综合={r.final_score:.2f}  关键词={r.keyword_score:.1f}  幻觉={r.hallucination_score:.1f}")
            print(f"     回答片段：{r.answer[:100]}...")

    # 构造返回结果
    report = {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0,
        "avg_final_score": round(avg_final, 2),
        "avg_keyword_score": round(avg_kw, 2),
        "avg_hallucination_score": round(avg_hal, 2),
        "by_category": {
            cat: {
                "count": s["count"],
                "passed": s["passed"],
                "pass_rate": round(s["passed"] / s["count"] * 100, 1) if s["count"] else 0,
                "avg_score": round(s["total_score"] / s["count"], 2) if s["count"] else 0,
            }
            for cat, s in by_category.items()
        },
        "details": [
            {
                "id": r.case_id,
                "category": r.category,
                "question": r.question,
                "answer": r.answer,
                "expected_type": r.expected_type,
                "reject_expected": r.reject_expected,
                "keyword_score": r.keyword_score,
                "hallucination_score": r.hallucination_score,
                "final_score": r.final_score,
                "keyword_hits": r.keyword_hits,
                "hallucination_reason": r.hallucination_reason,
            }
            for r in results
        ],
    }

    # 保存结果到 JSON
    out_dir = _ROOT / "evaluation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"eval_{ts}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存至：{out_file}")

    return report


def _parse_case_range(spec: str) -> List[int]:
    """解析题号范围，如 '1-10' 或 '11,13,15'。"""
    ids = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.extend(range(int(lo), int(hi) + 1))
        else:
            ids.append(int(part))
    return ids


def main():
    parser = argparse.ArgumentParser(description="企业报销 RAG 评测运行器")
    parser.add_argument(
        "--no-llm-check",
        action="store_true",
        help="跳过 LLM 幻觉检测（节省 API 调用）",
    )
    parser.add_argument(
        "--cases",
        type=str,
        default=None,
        help="指定题号范围，如 '1-10' 或 '11,13,15'",
    )
    parser.add_argument(
        "--standard-only",
        action="store_true",
        help="只跑 30 题标准测试集",
    )
    parser.add_argument(
        "--adversarial-only",
        action="store_true",
        help="只跑 4 题对抗性测试",
    )
    args = parser.parse_args()

    # 选择测试集
    if args.adversarial_only:
        cases = ADVERSARIAL_CASES
    elif args.standard_only:
        cases = STANDARD_CASES
    else:
        cases = ALL_CASES

    # 按题号筛选
    if args.cases:
        allowed = set(_parse_case_range(args.cases))
        cases = [c for c in cases if c["id"] in allowed]

    run_evaluation(
        api_key=ZHIPU_API_KEY,
        cases=cases if args.cases or args.standard_only or args.adversarial_only else None,
        enable_llm_check=not args.no_llm_check,
    )


if __name__ == "__main__":
    main()
