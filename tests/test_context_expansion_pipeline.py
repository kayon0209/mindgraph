"""PR-09 pipeline 集成：context_expansion 开关在检索工厂与管线层的生效契约。"""

from __future__ import annotations

from infrastructure import retrieval_factory
from retrieval.pipeline import RetrievalPipeline


class _StubDense:
    chunks: list = []
    metadata: dict = {"index_version": "test"}

    def search(self, query, top_k, **kwargs):
        return [], {}


class _StubSparse:
    def search(self, query, top_k, **kwargs):
        return [], {}


class _StubFusion:
    def fuse(self, lists, top_k):
        return []


def _pipeline(**kwargs) -> RetrievalPipeline:
    return RetrievalPipeline(_StubDense(), _StubSparse(), _StubFusion(), **kwargs)


def test_pipeline_context_expansion_off_by_default():
    """默认关闭：构造参数缺省即关闭（历史行为承诺）。"""
    pipeline = _pipeline()
    assert pipeline.context_expansion_enabled is False


def test_pipeline_trace_has_empty_expansion_when_disabled():
    """关闭时 trace.context_expansion 恒为空 dict（评测侧可据此区分）。"""
    trace = _pipeline().retrieve("测试问题", "hybrid")
    assert trace.context_expansion == {}


def test_factory_reads_settings_flag(monkeypatch):
    """工厂按 CONTEXT_EXPANSION_ENABLED 注入开关。"""
    from infrastructure.settings import get_settings

    monkeypatch.setenv("CONTEXT_EXPANSION_ENABLED", "true")
    get_settings.cache_clear()
    kwargs = retrieval_factory._context_expansion_kwargs()
    assert kwargs["context_expansion"] is True
    assert kwargs["context_expansion_max_chars"] == 1200
    get_settings.cache_clear()
    monkeypatch.delenv("CONTEXT_EXPANSION_ENABLED")
    get_settings.cache_clear()
    assert retrieval_factory._context_expansion_kwargs()["context_expansion"] is False
    get_settings.cache_clear()
