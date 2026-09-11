"""PR-05｜对冻结基线的 conflict 案例跑分层归因，产出归因报告与 backlog 输入。

用法::

    python scripts/attribute_conflict_cases.py \
        --dataset evaluation/datasets/mindgraph_golden_v2.jsonl \
        --predictions evaluation/results/answer_predictions_20260911T055750Z.jsonl

为什么单独成脚本：``conflict_accuracy`` 来自 **offline 冻结预测集**，不是
``/api/v1/evaluations`` 的检索层 run（``EvaluationService.execute()`` 不调用答案层），
所以归因必须走离线预测文件，指望 API 跑不出这个数。

输出写在 ``evaluation/results/conflict_attribution_<时间戳>.json``，
只增不改任何既有产物。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import sys

# ``evaluation`` 在项目根，``src`` 里的模块走另一个 pythonpath——两个都要。
_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evaluation.conflict_attribution import attribute_all  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "product" / "product.sqlite3"


def load_policy_versions(db_path: Path) -> dict[str, list[dict]]:
    """从 ``notes`` 表取每个 policy_key 的全部版本——必须与系统侧看到的一致。

    系统侧（``PolicyConflictService.find_for_policy_keys``）查的就是这张表，
    归因若用另一批数据就会失真。
    """
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT policy_key, vault_path, document_version, policy_status,"
            " effective_from, effective_to FROM notes"
            " WHERE policy_key IS NOT NULL AND policy_key != ''"
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["policy_key"]].append({
            "vault_path": row["vault_path"],
            "version": row["document_version"],
            "policy_status": row["policy_status"],
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"],
        })
    return dict(grouped)


def main() -> int:
    parser = argparse.ArgumentParser(description="分层归因 conflict 准确率失败案例")
    parser.add_argument("--dataset", default=str(PROJECT_ROOT / "evaluation/datasets/mindgraph_golden_v2.jsonl"))
    parser.add_argument("--predictions", required=True, help="离线答案预测集 .jsonl")
    parser.add_argument("--database", default=str(DEFAULT_DB))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "evaluation/results"))
    args = parser.parse_args()

    cases = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    predictions = [json.loads(line) for line in Path(args.predictions).read_text(encoding="utf-8").splitlines() if line.strip()]
    policy_versions = load_policy_versions(Path(args.database))

    report = attribute_all(cases, predictions, policy_versions=policy_versions)
    report["generated_at"] = datetime.now(UTC).isoformat()
    report["dataset"] = str(args.dataset)
    report["predictions"] = str(args.predictions)
    report["database"] = str(args.database)
    report["policy_key_count"] = len(policy_versions)
    report["multi_version_keys"] = sorted(k for k, v in policy_versions.items() if len(v) > 1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"conflict_attribution_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"conflict 案例数：{report['case_count']}")
    print(f"policy_key 总数：{report['policy_key_count']}｜多版本 key：{report['multi_version_keys']}")
    print(f"系统侧真缺陷：{report['system_defect_count']} / {report['case_count']}")
    print("\n主因分布：")
    for reason, count in sorted(report["by_primary_reason"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<32} {count}")
    print(f"\n冲突类别分布（scoring 分母 = {report['scoring_denominator']} / {report['case_count']}，"
          f"其余不适用、不计入 conflict_accuracy）：")
    for kind, count in sorted(report["by_conflict_kind"].items(), key=lambda kv: -kv[1]):
        print(f"  {kind:<32} {count}")
    print("\n逐案：")
    for item in report["results"]:
        flag = "缺陷" if item["is_system_defect"] else "口径"
        mark = "计分" if item["conflict_applicable"] else "不计分"
        print(f"  [{flag}/{mark}] {item['case_id']} -> {item['primary_reason']} ({item['conflict_kind']})"
              f"{' + ' + ','.join(item['secondary_reasons']) if item['secondary_reasons'] else ''}")
        if item["evidence"]["suppressed_versions"]:
            for version in item["evidence"]["suppressed_versions"]:
                print(f"        被排除版本：{version['policy_key']} v{version['version']}"
                      f" ({version['policy_status']}, effective_to={version['effective_to']})")
    print(f"\n报告已写入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
