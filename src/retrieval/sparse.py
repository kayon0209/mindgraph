from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from typing import Sequence

from .types import Chunk, RetrievalCandidate


_LATIN_OR_NUMBER = re.compile(r"[a-zA-Z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize_zh(text: str) -> list[str]:
    normalized = "".join(text.lower().split())
    cjk = "".join(_CJK.findall(normalized))
    tokens = list(cjk)
    tokens.extend(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    tokens.extend(_LATIN_OR_NUMBER.findall(normalized))
    return [token for token in tokens if token]


class BM25Retriever:
    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1, self.b = k1, b
        self._tokens = [tokenize_zh(chunk.text) for chunk in self.chunks]
        self._term_frequencies = [Counter(tokens) for tokens in self._tokens]
        self._doc_frequencies: dict[str, int] = defaultdict(int)
        for tokens in self._tokens:
            for token in set(tokens):
                self._doc_frequencies[token] += 1
        self._avg_length = sum(map(len, self._tokens)) / len(self._tokens) if self._tokens else 0.0

    def _score(self, query_tokens: Sequence[str], index: int) -> float:
        if not self.chunks or not query_tokens:
            return 0.0
        term_frequency = self._term_frequencies[index]
        length = len(self._tokens[index])
        score = 0.0
        for term in query_tokens:
            frequency = term_frequency.get(term, 0)
            if not frequency:
                continue
            document_frequency = self._doc_frequencies[term]
            inverse_document_frequency = math.log(1 + (len(self.chunks) - document_frequency + 0.5) / (document_frequency + 0.5))
            denominator = frequency + self.k1 * (1 - self.b + self.b * length / max(self._avg_length, 1.0))
            score += inverse_document_frequency * frequency * (self.k1 + 1) / denominator
        return score

    def search(self, query: str, top_k: int, access_scope: dict | None = None) -> tuple[list[RetrievalCandidate], dict[str, float]]:
        if top_k <= 0 or not query.strip() or not self.chunks:
            return [], {"bm25_retrieval_ms": 0.0}
        start = time.perf_counter()
        query_tokens = tokenize_zh(query)
        from application.access_control import chunk_acl_matches

        ranked = sorted(
            ((self._score(query_tokens, index), index) for index in range(len(self.chunks))
             if access_scope is None or chunk_acl_matches(self.chunks[index].metadata, access_scope)),
            key=lambda item: (-item[0], self.chunks[item[1]].chunk_id),
        )[:top_k]
        elapsed = (time.perf_counter() - start) * 1000
        results = [
            RetrievalCandidate(chunk=self.chunks[index], sparse_score=float(score), sparse_rank=rank)
            for rank, (score, index) in enumerate(ranked, 1)
            if score > 0
        ]
        return results, {"bm25_retrieval_ms": round(elapsed, 3)}
