"""Cross-Encoder 重排的模型加载解析：本地目录优先，其次 HF 仓库。

## 为什么需要这层解析

``RERANKER_LOCAL_FILES_ONLY`` 默认为 True。若本地没有权重，``CrossEncoder``
会在加载期抛 ``OSError``，管线把它记为降级
（``degradation_reason=reranker_error``），于是 ``hybrid_rerank`` 整批降级、
指标与 ``hybrid`` **逐位相同**，而产物里看不出"其实一次重排都没跑"
（实测见 ``evaluation/results/retrieval_v2/comparison_20260911T122858Z.json``：
``degraded_queries=23/23``）。本模块锁的是加载解析契约——与
``BGEEmbeddingProvider`` 的 ``BGE_LOCAL_PATH`` 同构：本地目录存在就直接用，
不存在才回落到 HF 仓库名。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrieval import reranker as reranker_module
from retrieval.reranker import CrossEncoderReranker
from retrieval.types import Chunk, RetrievalCandidate

REPO_ID = "BAAI/bge-reranker-base"


class _FakeCrossEncoder:
    """记录构造参数的假 CrossEncoder，避免测试触发真实权重下载。"""

    instances: list[tuple[str, dict]] = []

    def __init__(self, model_name_or_path: str, **kwargs) -> None:
        type(self).instances.append((model_name_or_path, kwargs))

    def predict(self, pairs):
        return [0.9 - 0.1 * index for index, _ in enumerate(pairs)]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _FakeCrossEncoder.instances = []
    monkeypatch.setattr(reranker_module, "_lazy_import_ce", lambda: _FakeCrossEncoder)
    monkeypatch.delenv("RERANKER_LOCAL_PATH", raising=False)
    yield
    _FakeCrossEncoder.instances = []


def _candidate(chunk_id: str, text: str) -> RetrievalCandidate:
    return RetrievalCandidate(chunk=Chunk(chunk_id=chunk_id, text=text, document_id="d", chunk_index=0, section_path=None))


def test_local_directory_takes_precedence(tmp_path: Path) -> None:
    local = tmp_path / "bge-reranker-base"
    local.mkdir()

    CrossEncoderReranker(local_path=str(local)).rerank("差旅标准", [_candidate("c1", "差旅费每日500元")], 1)

    assert len(_FakeCrossEncoder.instances) == 1
    loaded_path, _kwargs = _FakeCrossEncoder.instances[0]
    assert loaded_path == str(local)


def test_missing_local_directory_falls_back_to_repo_id(tmp_path: Path) -> None:
    CrossEncoderReranker(local_path=str(tmp_path / "absent"), local_files_only=True).rerank(
        "差旅标准", [_candidate("c1", "差旅费每日500元")], 1,
    )

    loaded_path, kwargs = _FakeCrossEncoder.instances[0]
    assert loaded_path == REPO_ID
    assert kwargs["local_files_only"] is True


def test_local_path_can_come_from_environment(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "env-model"
    local.mkdir()
    monkeypatch.setenv("RERANKER_LOCAL_PATH", str(local))

    CrossEncoderReranker().rerank("差旅标准", [_candidate("c1", "差旅费每日500元")], 1)

    assert _FakeCrossEncoder.instances[0][0] == str(local)


def test_repo_id_still_exposed_as_model_name(tmp_path: Path) -> None:
    """评测产物用 ``reranker.model_name`` 记录模型标识，契约不能变。"""
    reranker = CrossEncoderReranker(local_path=str(tmp_path / "absent"))
    assert reranker.model_name == REPO_ID


def test_rerank_orders_by_score_and_stamps_final_rank() -> None:
    """排序契约：按分数降序、写回 reranker_score/final_rank、不修改入参对象。"""
    candidates = [_candidate("c1", "甲"), _candidate("c2", "乙")]
    top = CrossEncoderReranker(local_path="missing-on-purpose").rerank("查询", candidates, 2)

    assert [item.chunk.chunk_id for item in top] == ["c1", "c2"]
    assert [item.final_rank for item in top] == [1, 2]
    assert top[0].reranker_score is not None and top[1].reranker_score is not None
    assert top[0].reranker_score > top[1].reranker_score
    assert all(item.final_rank is None for item in candidates), "入参候选不得被就地改写"
