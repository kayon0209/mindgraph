"""索引根登记表门禁（2026-09-11）。

背景：``data/mindgraph_indexes`` 与 ``data/retrieval_indexes`` **都叫 CURRENT**，
服务两套互不相干的评测栈，而且各自完全自洽——实测标签交集为 0。所以问题不是
"哪个根是错的"，而是"接错栈是静默的"：表现为跑得通、数字正常，但测的不是
线上系统。

本文件把三件事变成机器可判据：

1. 登记的根与它绑定的数据集在磁盘上都存在；
2. 每个根与它声明的数据集的 gold 标签 **交集 > 0**（这就是"根选对了"的定义）；
3. 磁盘上每个索引根都在登记表里（新增根漏登记 → 红）。

外加两条反向断言，把两个具体结论钉死在测试里：

- 把 ``EvaluationService`` 的根换成线上根（= "统一根"方案）会立刻坏；
- golden v2 与历史评测根无关（两栈数据粒度不同）。

测试只读磁盘现状，不写任何文件。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from application.index_metadata import (
    INDEX_ROOT_REGISTRY,
    dataset_gold_labels,
    index_label_set,
    index_root_binding_report,
    index_root_spec,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = PROJECT_ROOT / "evaluation" / "datasets"
LEGACY_DATASET = "expense_qa_v1.jsonl"
GOLDEN_V2_DATASET = "mindgraph_golden_v2.jsonl"


def test_registry_is_not_empty_and_names_are_unique() -> None:
    names = [spec.name for spec in INDEX_ROOT_REGISTRY]
    assert names, "索引根登记表不能为空"
    assert len(names) == len(set(names)), f"登记表出现重名：{names}"


def test_every_registered_root_exists_on_disk() -> None:
    for spec in INDEX_ROOT_REGISTRY:
        root = PROJECT_ROOT / spec.root
        assert root.is_dir(), f"登记的根不存在：{spec.root}"
        assert (root / "CURRENT").is_file(), f"{spec.root} 没有 CURRENT"


def test_every_registered_dataset_exists_on_disk() -> None:
    for spec in INDEX_ROOT_REGISTRY:
        assert (DATASET_DIR / spec.dataset).is_file(), (
            f"{spec.name} 绑定的数据集缺失：{spec.dataset}"
        )


@pytest.mark.parametrize("spec", INDEX_ROOT_REGISTRY, ids=lambda spec: spec.name)
def test_root_label_set_intersects_its_dataset(spec) -> None:
    """核心门禁：每个根必须与它声明的数据集有标签交集。

    这是"根服务的是这套数据集"的机器判据。哪天有人换了根、或换回一个与当前
    数据集不匹配的历史版本，这条会红——而不是产出一个看着正常的指标。
    """
    labels, version, chunk_count = index_label_set(PROJECT_ROOT / spec.root, spec.label_key)
    gold = dataset_gold_labels(DATASET_DIR / spec.dataset, spec.label_key)

    assert version, f"{spec.root} 没有可读的 CURRENT"
    assert chunk_count > 0, f"{spec.root} 的活跃索引为空"
    assert labels, f"{spec.root} 没读出任何 {spec.label_key} 标签"
    assert gold, f"{spec.dataset} 没读出任何 gold 标签（字段：{spec.label_key}）"

    overlap = labels & gold
    assert overlap, (
        f"{spec.root}（{chunk_count} chunks）与它声明的 {spec.dataset} "
        f"标签交集为 0 —— 绑定关系不成立，根或数据集被换过"
    )


def test_every_on_disk_index_root_is_registered() -> None:
    """`data/` 下每个索引根都必须登记，新增根漏登记就红。"""
    data_dir = PROJECT_ROOT / "data"
    declared = {spec.name for spec in INDEX_ROOT_REGISTRY}
    on_disk = {
        path.name
        for path in data_dir.iterdir()
        if path.is_dir() and (path / "CURRENT").is_file()
    }
    assert on_disk <= declared, f"未登记的索引根：{sorted(on_disk - declared)}"


def test_index_root_spec_rejects_unknown_name() -> None:
    with pytest.raises(KeyError, match="unregistered index root"):
        index_root_spec("no-such-root")


def test_binding_report_marks_every_root_ok() -> None:
    report = index_root_binding_report(PROJECT_ROOT)
    assert report["binding_ok"] is True
    assert {entry["name"] for entry in report["roots"]} == {
        spec.name for spec in INDEX_ROOT_REGISTRY
    }
    for entry in report["roots"]:
        assert entry["exists"] is True
        assert entry["overlap"] > 0, f"{entry['name']} 与 {entry['dataset']} 无交集"


def test_legacy_and_online_label_spaces_do_not_intersect() -> None:
    """两栈的标签空间不相交 —— 这正是必须「显式分派」而不能「统一根」的原因。

    ``mg-`` 根的 chunk_id 是 32 位 hex，而 ``expense_qa_v1`` 的 gold 是
    ``差旅费报销管理办法.md::16`` 这类中文 chunk_id，交集为 0。

    2026-09-11 迁移前，这里的结论是「把 ``EvaluationService`` 的根统一到线上根会直接
    坏掉」。迁移后 ``EvaluationService`` 改成**按 dataset 显式分派**（每个栈读自己的
    根与标签口径），「换根」这个动作本身不再存在。本断言保留，钉死那个前提：
    哪天交集不为 0 了，分派设计才需要重新评估。
    """
    production_root = PROJECT_ROOT / index_root_spec("mindgraph_indexes").root
    labels, _, _ = index_label_set(production_root, "chunk_id")
    gold = dataset_gold_labels(DATASET_DIR / LEGACY_DATASET, "chunk_id")

    assert labels, "线上根没读出 chunk_id 标签，本断言的证据链不成立"
    assert gold, f"{LEGACY_DATASET} 没读出 gold_chunk_ids，本断言的证据链不成立"
    assert not (labels & gold), (
        "线上根竟然与 expense_qa_v1 的 gold chunk_id 有交集 —— "
        "两栈开始共享标签空间，按 dataset 分派的设计需要重新评估"
    )


def test_golden_v2_labels_absent_from_legacy_root() -> None:
    """golden v2 与历史评测根无关：两栈的"期望证据"粒度本就不同。"""
    legacy_root = PROJECT_ROOT / index_root_spec("retrieval_indexes").root
    labels, _, _ = index_label_set(legacy_root, "vault_path")
    gold = dataset_gold_labels(DATASET_DIR / GOLDEN_V2_DATASET, "vault_path")

    assert gold, f"{GOLDEN_V2_DATASET} 没读出 gold_vault_paths"
    assert not (labels & gold), (
        "历史评测根出现了 golden v2 的 vault_path —— 两栈开始共享标签空间，需重新评估"
    )
