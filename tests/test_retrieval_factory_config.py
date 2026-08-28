from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

import infrastructure.retrieval_factory as retrieval_factory
import local_embedder
from retrieval.embeddings import DEFAULT_BGE_MODEL


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **values) -> None:
    """工厂统一经 get_settings() 读取检索配置（进程环境变量 > .env > 默认）。"""
    defaults = {"RERANK_TOP_N": 10, "RETRIEVAL_CANDIDATE_COUNT": 20}
    defaults.update(values)
    monkeypatch.setattr(
        retrieval_factory,
        "get_settings",
        lambda: SimpleNamespace(**defaults),
    )


def test_rerank_top_n_reads_the_declared_settings_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, RERANK_TOP_N=7)

    assert retrieval_factory._rerank_top_n() == 7


def test_rerank_top_n_rejects_non_positive_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, RERANK_TOP_N=0)

    with pytest.raises(ValueError, match="RERANK_TOP_N"):
        retrieval_factory._rerank_top_n()


def test_legacy_local_embedder_uses_the_same_configurable_bge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BGE_MODEL_NAME", "test/custom-bge")
    reloaded = importlib.reload(local_embedder)
    assert reloaded.MODEL_NAME == "test/custom-bge"

    monkeypatch.delenv("BGE_MODEL_NAME")
    reloaded = importlib.reload(local_embedder)
    assert reloaded.MODEL_NAME == DEFAULT_BGE_MODEL
