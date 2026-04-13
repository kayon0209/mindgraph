"""本地 Embedding 模型（BAAI/bge-large-zh-v1.5），无需 API 调用。"""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

# 使用中文效果最好的开源模型
# 首次运行会自动下载（约 1.2GB）
MODEL_NAME = "BAAI/bge-large-zh-v1.5"

# 全局缓存，避免重复加载
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """获取或加载模型（延迟加载，单例模式）。"""
    global _model
    if _model is None:
        print(f"正在加载本地 Embedding 模型: {MODEL_NAME}...")
        _model = SentenceTransformer(MODEL_NAME)
        print(f"模型加载完成，维度: {_model.get_sentence_embedding_dimension()}")
    return _model


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """
    批量将文本转换为向量。
    
    Args:
        texts: 文本列表
        
    Returns:
        向量列表，每个向量是 float 列表
    """
    if not texts:
        return []
    
    model = _get_model()
    
    # BGE 模型需要在文本前加指令前缀以获得最佳效果
    # 对于检索任务，使用 "Represent this sentence for searching relevant passages:"
    instruction = "Represent this sentence for searching relevant passages:"
    
    # 为每个文本添加指令前缀
    texts_with_instruction = [f"{instruction}{t}" for t in texts]
    
    # 计算 embedding
    embeddings = model.encode(
        texts_with_instruction,
        normalize_embeddings=True,  # 归一化，便于余弦相似度计算
        convert_to_numpy=True,
    )
    
    # 转换为 Python list
    return embeddings.tolist()


def embed_query(text: str) -> List[float]:
    """将单个查询文本转换为向量。"""
    return embed_texts([text])[0]


def get_dimension() -> int:
    """获取向量维度。"""
    model = _get_model()
    return model.get_sentence_embedding_dimension()


if __name__ == "__main__":
    # 测试
    test_texts = ["差旅费报销", "加班餐费标准"]
    embeddings = embed_texts(test_texts)
    print(f"测试文本数: {len(embeddings)}")
    print(f"向量维度: {len(embeddings[0])}")
    print(f"示例向量前5维: {embeddings[0][:5]}")
