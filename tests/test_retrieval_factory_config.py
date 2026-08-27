from __future__ import annotations

import importlib

import pytest

import infrastructure.retrieval_factory as retrieval_factory
import local_embedder
from retrieval.embeddings import DEFAULT_BGE_MODEL


def test_rerank_top_n_uses_the_declared_environment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_TOP_N", "7")
    monkeypatch.setenv("RETRANK_TOP_N", "19")

    assert retrieval_factory._rerank_top_n() == 7


def test_rerank_top_n_rejects_non_positive_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_TOP_N", "0")

    with pytest.raises(ValueError, match="RERANK_TOP_N"):
        retrieval_factory._rerank_top_n()


def test_legacy_local_embedder_uses_the_same_configurable_bge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BGE_MODEL_NAME", "test/custom-bge")
    reloaded = importlib.reload(local_embedder)
    assert reloaded.MODEL_NAME == "test/custom-bge"

    monkeypatch.delenv("BGE_MODEL_NAME")
    reloaded = importlib.reload(local_embedder)
    assert reloaded.MODEL_NAME == DEFAULT_BGE_MODEL
