"""
统一 Embedding 接口，支持多种后端：
- zhipu: 智谱 embedding-3（需要 API Key，效果最佳）
- local: 本地 BGE 模型（免费，需要下载 1.2GB）
- hash: 轻量本地字符 n-gram Hash Embedding（无需外部依赖，兜底可用）
- openai: OpenAI embedding（需要 API Key）

通过 .env 中的 EMBED_BACKEND 配置，默认使用 zhipu。
"""
from __future__ import annotations

import os
import hashlib
from typing import List, Sequence

# 全局后端实例
_backend = None


def _get_backend():
    """获取或初始化 embedding 后端。"""
    global _backend
    if _backend is None:
        backend_type = os.getenv("EMBED_BACKEND", "zhipu").lower()
        
        if backend_type == "local":
            from local_embedder import embed_texts, embed_query
            _backend = _LocalBackend(embed_texts, embed_query)
        elif backend_type == "hash":
            _backend = _HashBackend()
        elif backend_type == "openai":
            _backend = _OpenAIBackend()
        else:  # 默认 zhipu
            _backend = _ZhipuBackend()
        
        print(f"[Embedder] 使用后端: {backend_type}")
    
    return _backend


class _ZhipuBackend:
    """智谱 embedding-3 后端。"""
    
    def embed_texts(self, client, texts: Sequence[str]) -> List[List[float]]:
        from config import EMBED_MODEL
        if not texts:
            return []
        out = []
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch = list(texts[i:i + batch_size])
            resp = client.embeddings.create(input=batch, model=EMBED_MODEL)
            ordered = sorted(resp.data, key=lambda x: x.index if x.index is not None else 0)
            out.extend([item.embedding for item in ordered])
        return out
    
    def embed_query(self, client, text: str) -> List[float]:
        return self.embed_texts(client, [text])[0]


class _LocalBackend:
    """本地 BGE 模型后端。"""
    
    def __init__(self, embed_texts_fn, embed_query_fn):
        self.embed_texts_fn = embed_texts_fn
        self.embed_query_fn = embed_query_fn
    
    def embed_texts(self, client, texts: Sequence[str]) -> List[List[float]]:
        # client 参数被忽略，本地模型不需要
        return self.embed_texts_fn(texts)
    
    def embed_query(self, client, text: str) -> List[float]:
        # client 参数被忽略，本地模型不需要
        return self.embed_query_fn(text)


class _OpenAIBackend:
    """OpenAI embedding 后端（预留）。"""
    
    def embed_texts(self, client, texts: Sequence[str]) -> List[List[float]]:
        # TODO: 实现 OpenAI embedding
        raise NotImplementedError("OpenAI backend not implemented yet")
    
    def embed_query(self, client, text: str) -> List[float]:
        return self.embed_texts(client, [text])[0]


class _HashBackend:
    """轻量本地 Embedding 后端，基于字符 n-gram 哈希，适合作为无额度/无 torch 时的兜底。"""

    dim = 1024

    def _embed_one(self, text: str) -> List[float]:
        import math

        normalized = "".join((text or "").lower().split())
        if not normalized:
            return [0.0] * self.dim

        features = []
        for n in (1, 2, 3):
            if len(normalized) >= n:
                features.extend(normalized[i : i + n] for i in range(len(normalized) - n + 1))

        vec = [0.0] * self.dim
        for token in features:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign

        norm = math.sqrt(sum(x * x for x in vec))
        if norm:
            vec = [x / norm for x in vec]
        return vec

    def embed_texts(self, client, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, client, text: str) -> List[float]:
        return self._embed_one(text)


def embed_texts(client, texts: Sequence[str]) -> List[List[float]]:
    """批量向量化。"""
    return _get_backend().embed_texts(client, texts)


def embed_query(client, text: str) -> List[float]:
    """单个查询向量化。"""
    return _get_backend().embed_query(client, text)


def get_backend_type() -> str:
    """获取当前使用的后端类型。"""
    return os.getenv("EMBED_BACKEND", "zhipu").lower()
