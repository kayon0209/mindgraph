from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Any

from application.chat_service import ChatService
from application.document_lifecycle_service import DocumentLifecycleService
from application.evaluation_governance_service import EvaluationGovernanceService
from application.evaluation_service import EvaluationService
from application.feedback_service import FeedbackService
from application.index_lifecycle_service import IndexLifecycleService
from application.knowledge_service import KnowledgeService
from application.mindgraph_graph_store import MindGraphGraphStore
from application.relation_extraction_service import RelationExtractionService
from infrastructure.anthropic_provider import AnthropicProvider
from infrastructure.chat_provider import ZhipuChatProvider
from infrastructure.database import ProductDatabase
from infrastructure.openai_compatible_provider import OpenAICompatibleProvider
from infrastructure.provider_registry import ProviderRegistry
from infrastructure.retrieval_factory import INDEX_ROOT, create_mindgraph_retrieval_pipeline, create_retrieval_pipeline
from infrastructure.settings import get_settings

logger = logging.getLogger("mindgraph.dependencies")
_settings = get_settings()


class ServiceContainer:
    def __init__(self) -> None:
        settings = get_settings()
        # PROJECT_ROOT: dependencies.py is in src/api/, three parents up = project root
        self.root = Path(__file__).resolve().parent.parent.parent
        DOCS_DIR = self.root / "knowledge"
        UPLOAD_DIR = self.root / "data" / "uploads"

        self.database = ProductDatabase(self.root / "data" / "product" / "product.sqlite3")
        self.database.initialize()
        self.database.mark_abandoned_runs_interrupted()
        self._pipelines = {}
        providers = [
            ZhipuChatProvider(settings.ZHIPU_API_KEY, settings.ZHIPU_MODEL, settings.ZHIPU_VERIFIED),
            OpenAICompatibleProvider(
                settings.OPENAI_COMPAT_PROVIDER_NAME,
                settings.OPENAI_COMPAT_BASE_URL,
                settings.OPENAI_COMPAT_API_KEY,
                settings.OPENAI_COMPAT_MODEL,
                settings.CHAT_TIMEOUT_SECONDS,
                settings.CHAT_MAX_RETRIES,
                settings.OPENAI_COMPAT_VERIFIED,
                [item.strip() for item in settings.OPENAI_COMPAT_MODELS.split(",") if item.strip()],
            ),
            AnthropicProvider(settings.ANTHROPIC_API_KEY, settings.ANTHROPIC_MODEL),
        ]
        self.provider_registry = ProviderRegistry(providers, settings.CHAT_PROVIDER)
        self.provider = self.provider_registry.get()
        self.privacy_log = settings.PRIVACY_LOG_QUESTIONS
        self.chat = ChatService(self.database, self.pipeline, self.provider_registry, self.privacy_log)
        self.knowledge = KnowledgeService(DOCS_DIR, UPLOAD_DIR, INDEX_ROOT, self.invalidate_pipelines)
        self.document_lifecycle = DocumentLifecycleService(self.database, self.root / "data" / "product" / "documents")
        self.document_lifecycle.import_existing_markdown(list(DOCS_DIR.glob("*.md")))
        self.index_lifecycle = IndexLifecycleService(self.database, self.document_lifecycle, INDEX_ROOT, self.invalidate_pipelines)
        self.feedback = FeedbackService(self.database)
        self.evaluation = EvaluationService(self.database)
        self.governance = EvaluationGovernanceService(self.database)
        self._register_builtin_datasets()
        self._init_mindgraph()

    def _init_mindgraph(self) -> None:
        """装配 MindGraph Graph RAG 管线（复用 ChatService + MindGraph 检索包装）。"""
        settings = get_settings()
        self.mindgraph_index_root = self.root / "data" / "mindgraph_indexes"
        self.mindgraph_graph_store = MindGraphGraphStore(self.database)
        self.relation_extraction = RelationExtractionService(
            self.database, self.mindgraph_index_root, self.provider_registry
        )
        self._mindgraph_pipelines: dict[tuple[int, bool], Any] = {}
        self.mindgraph_chat = ChatService(
            self.database,
            self.mindgraph_pipeline,
            self.provider_registry,
            self.privacy_log,
            system_prompt=(
                "你是个人知识助手 MindGraph。只能依据给定证据回答；不得编造。"
                "先给结论，再给简要依据，并使用 [citation-N] 标注引用来源。"
            ),
        )
        # 本地目录 / Markdown 目录增量同步连接器（Phase 5-2）
        from application.directory_connector_service import DirectoryConnectorService
        from application.mindgraph_index_service import MindGraphIndexService
        self.mindgraph_index_service = MindGraphIndexService(
            self.database,
            self.root / "knowledge",
            self.mindgraph_index_root,
            on_activated=self.invalidate_pipelines,
        )
        self.directory_connector = DirectoryConnectorService(
            self.database,
            self.root / "knowledge",
            self.mindgraph_index_service,
            allowed_roots=(self.root / "knowledge", *settings.connector_allowed_root_list),
        )

    def mindgraph_pipeline(self, top_k: int, graph_enabled: bool = True):
        key = (top_k, graph_enabled)
        if key not in self._mindgraph_pipelines:
            self._mindgraph_pipelines[key] = create_mindgraph_retrieval_pipeline(
                self.mindgraph_index_root, self.mindgraph_graph_store, top_k, graph_enabled
            )
        return self._mindgraph_pipelines[key]

    def _register_builtin_datasets(self) -> None:
        path = self.root / "evaluation" / "datasets" / "expense_qa_v1.jsonl"
        if not path.exists():
            return
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for split, dataset_type in (("development", "development"), ("regression", "regression")):
            dataset_id = f"expense_qa_{split}"
            if not self.database.fetch_one("SELECT 1 FROM datasets WHERE dataset_id=? AND version=?", (dataset_id, "1.0.0")):
                self.governance.register_dataset(dataset_id, "1.0.0", dataset_type,
                    f"Existing Milestone 1 {split} split", [item for item in cases if item["split"] == split],
                    "approved", "Imported without changing fixed split labels")
        if not self.database.fetch_one("SELECT 1 FROM datasets WHERE dataset_id='expense_qa_holdout' AND version='0.1.0'"):
            self.governance.register_dataset("expense_qa_holdout", "0.1.0", "holdout",
                "Independent future validation; no cases supplied", [], "incomplete",
                "Structure only; no fabricated cases or labels")

    def pipeline(self, top_k: int):
        if top_k not in self._pipelines:
            self._pipelines[top_k] = create_retrieval_pipeline(top_k)
        return self._pipelines[top_k]

    def invalidate_pipelines(self) -> None:
        self._pipelines.clear()
        getattr(self, "_mindgraph_pipelines", {}).clear()


_override: ServiceContainer | None = None


@lru_cache(maxsize=1)
def _build_container() -> ServiceContainer:
    return ServiceContainer()


def get_container() -> ServiceContainer:
    return _override or _build_container()


def override_container(container: ServiceContainer | None) -> None:
    global _override
    _override = container
    if container is None:
        _build_container.cache_clear()
