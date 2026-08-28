"""本地 Embedding 模型（与当前检索配置保持一致），无需 API 调用。"""
from __future__ import annotations

import logging
import os
from typing import List, Sequence, cast

from retrieval.embeddings import DEFAULT_BGE_MODEL

logger = logging.getLogger("mindgraph.embedding.local")
MODEL_NAME = os.getenv("BGE_MODEL_NAME", DEFAULT_BGE_MODEL)

# 全局缓存，避免重复加载
_model = None


def _get_model():
    """获取或加载模型（延迟加载，单例模式）。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("loading_local_embedding_model", extra={"model": MODEL_NAME})
        _model = SentenceTransformer(MODEL_NAME)
        logger.info(
            "local_embedding_model_loaded",
            extra={"model": MODEL_NAME, "dimension": _model.get_sentence_embedding_dimension()},
        )
    return _model


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """
    批量将文本转换为向量（文档侧 — 不加指令前缀）。
    
    Args:
        texts: 文本列表
        
    Returns:
        向量列表，每个向量是 float 列表
    """
    if not texts:
        return []
    
    model = _get_model()
    
    # 文档 embedding 不加指令前缀（BGE 模型指令仅用于查询侧）
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    
    return cast(List[List[float]], embeddings.tolist())


def embed_query(text: str) -> List[float]:
    """将单个查询文本转换为向量（加 BGE 中文查询指令）。"""
    if not text:
        return []
    model = _get_model()
    # BGE 中文模型查询指令
    instruction = "为这个句子生成表示以用于检索相关文章："
    embedding = model.encode(
        [instruction + text],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return cast(List[float], embedding[0].tolist())


def get_dimension() -> int:
    """获取向量维度。"""
    model = _get_model()
    return int(model.get_sentence_embedding_dimension())


if __name__ == "__main__":
    # 测试
    test_texts = ["差旅费报销", "加班餐费标准"]
    embeddings = embed_texts(test_texts)
    print(f"测试文本数: {len(embeddings)}")
    print(f"向量维度: {len(embeddings[0])}")
    print(f"示例向量前5维: {embeddings[0][:5]}")
