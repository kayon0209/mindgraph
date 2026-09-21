from __future__ import annotations

import os
from copy import deepcopy
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

from .types import RetrievalCandidate


def _lazy_import_ce():
    """Lazy import to avoid blocking at module load time."""
    from sentence_transformers import CrossEncoder as CE
    return CE


DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"


def _default_local_path() -> str:
    """``<project>/data/bge-reranker-base``。

    与 ``BGEEmbeddingProvider`` 的默认本地目录（``data/bge-small-zh-v1.5``）同构：
    仓库内自带权重目录时直接加载，绕开 Windows 上 HF 缓存损坏与镜像不可达的问题。
    """
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "bge-reranker-base",
    )


class CrossEncoderReranker:
    """Cross-Encoder 精排；权重解析顺序：显式参数 > 环境变量 > 项目内 data 目录 > HF 仓库。

    为什么要本地目录优先：``RERANKER_LOCAL_FILES_ONLY`` 默认 True，本地无权重时
    ``CrossEncoder`` 会抛 ``OSError``，管线记为降级——检索仍能出结果，但
    ``hybrid_rerank`` 会**静默退化成 hybrid**（评测产物里只体现为
    ``degraded_queries`` 计数，指标与 hybrid 逐位相同），看起来"重排跑了"其实没跑。
    """

    def __init__(self, model_name: str | None = None, local_files_only: bool | None = None, local_path: str | None = None) -> None:
        self._model_name = model_name or os.getenv("RERANKER_MODEL_NAME", DEFAULT_RERANKER_MODEL)
        self._local_files_only = local_files_only if local_files_only is not None else os.getenv("RERANKER_LOCAL_FILES_ONLY", "true").lower() == "true"
        # 本地模型目录优先（显式参数 > RERANKER_LOCAL_PATH > <project>/data/bge-reranker-base）。
        self._local_path = local_path or os.getenv("RERANKER_LOCAL_PATH") or _default_local_path()
        self._model = None
        self._loaded_from_local = False

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def loaded_from_local(self) -> bool:
        return self._loaded_from_local

    def _load(self):
        if self._model is None:
            CE = _lazy_import_ce()
            try:
                if self._local_path and os.path.isdir(self._local_path):
                    self._model = CE(self._local_path)
                    self._loaded_from_local = True
                else:
                    self._model = CE(self._model_name, local_files_only=self._local_files_only)
                    self._loaded_from_local = False
            except OSError as exc:
                # 这是本仓库最容易踩且最难自查的一处配置问题：默认
                # local_files_only=True + 权重没放进 data/ → 整条 hybrid_rerank
                # 静默降级。本地目录存在但内容损坏（如缺 model.safetensors）也走这里，
                # 所以 try 必须同时包住本地与 HF 两条加载路径。
                # 把"该把权重放哪"直接写进异常，省掉一轮猜测。
                raise OSError(
                    f"Reranker 权重不可用：model={self._model_name} "
                    f"local_files_only={self._local_files_only} local_path={self._local_path}。"
                    "请将权重目录放到 local_path，或设置 RERANKER_LOCAL_PATH，"
                    "或允许联网下载（local_files_only=False）。"
                    f"原始错误：{exc}"
                ) from exc
        return self._model

    def rerank(self, query: str, candidates: Sequence[RetrievalCandidate], top_k: int) -> list[RetrievalCandidate]:
        if not candidates or top_k <= 0:
            return []
        # ⚠️ 这里的历史反复横跳过一次，把结论写在代码里免得又被改回去：
        # 曾以为"批量推理导致段错误"并改成逐条 predict，依据是 batch=1 成功 8/8；
        # 后来发现 ``dense.search(query, 1)`` 恒返回 0 个候选，rerank 根本没执行，
        # 那 8/8 是**空跑**。批量 vs 逐条没有可信差异，已回退为批量（快约 2×）。
        #
        # 真正的问题是**环境内存/页面文件不足**：C 盘仅剩 ~3.6GB，Windows 页面文件
        # 在 C 盘，加载 1.1GB 权重（safetensors mmap）时贴着上限，症状随机表现为
        # exit 139 / access violation（无 traceback），最直白的一次是 OSError 1455
        # 「页面文件太小」抛在 safe_open 处。缓解靠 config.py 里的 OMP_NUM_THREADS=1
        # 与 HF_DEACTIVATE_ASYNC_LOAD=1（降内存峰值）；彻底解决需把页面文件迁到
        # D 盘（19GB 可用），那是系统级改动，不由代码决定。
        scores = self._load().predict([(query, candidate.chunk.text) for candidate in candidates])
        ranked = sorted(
            ((float(score), deepcopy(candidate)) for score, candidate in zip(scores, candidates)),
            key=lambda item: (-item[0], item[1].chunk.chunk_id),
        )[:top_k]
        results = []
        for rank, (score, candidate) in enumerate(ranked, 1):
            candidate.reranker_score, candidate.final_rank = score, rank
            results.append(candidate)
        return results
