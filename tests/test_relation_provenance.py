"""关系抽取的产出溯源：DB 里必须能看出每条关系**是否经 LLM 判定**。

背景：``--use-llm`` 跑出来的 39 条关系，DB 里记的是
``extraction_method='embedding'`` / ``model_version='auto-v2'``——与"纯向量相似度
直接产出"完全无法区分。provider 不可用时 ``_llm_refine`` 会静默原样保留规则候选，
也就是说"声称用了 LLM"和"其实没用"在落库后长得一模一样。这个项目的核心卖点是
证据链可追溯，自证不了就是硬伤。

约定：**只有真正调用成功**的候选才带 ``llm_model`` 标记，落库时据此拼后缀。
"""

from __future__ import annotations

from application.relation_extraction_service import (
    MODEL_VERSION,
    _relation_method,
    _relation_model_version,
)


def test_rule_only_candidate_keeps_plain_labels() -> None:
    """未经 LLM 判定的候选（provider 缺失 / 调用失败）不得带 llm 后缀。"""
    candidate = {"signal": "embedding", "confidence": 0.82}

    assert _relation_method(candidate) == "embedding"
    assert _relation_model_version(candidate) == MODEL_VERSION


def test_llm_judged_candidate_is_marked_with_model_name() -> None:
    candidate = {"signal": "embedding", "llm_model": "qwen3.8-flash", "confidence": 0.9}

    assert _relation_method(candidate) == "embedding+llm"
    assert _relation_model_version(candidate) == f"{MODEL_VERSION}+llm:qwen3.8-flash"


def test_tag_signal_candidate_is_marked_the_same_way() -> None:
    candidate = {"signal": "tag", "llm_model": "qwen3.8-flash"}

    assert _relation_method(candidate) == "tag+llm"


def test_missing_signal_falls_back_to_unknown() -> None:
    assert _relation_method({}) == "unknown"


# ── LLM prompt 必须引导「业务断言类型」而不是「主题相近」 ─────────────────
# 背景：旧 prompt 只给 related_to|references|contradicts|elaborates 四个通用类型，
# LLM 于是把"同一文档的不同抓取记录"这类**同源重复**判成高相似关系（实测 39 条里
# confidence 最高的 4 条全是这类），而图扩展需要的是 EXCEPTION_TO / SUPERSEDES /
# HAS_LIMIT 等可推理的断言类型。


def _capture_prompt() -> str:
    from pathlib import Path

    from application.relation_extraction_service import RelationExtractionService

    captured: dict[str, str] = {}

    class _RecordingProvider:
        model_name = "test-model"

        def complete(self, messages):  # noqa: ANN001
            captured["system"] = messages[0]["content"]
            return '{"related": true, "relation_type": "SUPERSEDES", "reason": "新制度取代旧制度"}', {}

    service = RelationExtractionService(db=None, index_root=Path("."), provider_registry=None)
    decision = service._ask_llm(_RecordingProvider(), "标题A", "片段A", "标题B", "片段B")
    assert decision["relation_type"] == "SUPERSEDES"
    return captured["system"]


def test_prompt_offers_typed_assertion_relations() -> None:
    system = _capture_prompt()
    for relation_type in ("EXCEPTION_TO", "SUPERSEDES", "HAS_LIMIT", "REQUIRES_APPROVAL", "APPLIES_TO", "CONTRADICTS"):
        assert relation_type in system, relation_type


def test_prompt_excludes_same_source_duplicates() -> None:
    """同源重复（同一文档的不同抓取/版本片段）必须被显式排除。"""
    system = _capture_prompt()
    assert "不构成" in system
    assert "抓取" in system
