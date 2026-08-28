"""阶段B：问题概念挖掘（QuestionConceptMiner）单元测试 + 端点集成测试。

覆盖：
- 《标题》精确匹配命中笔记；未匹配 《...》 累计为 concept_signals 覆盖缺口；
- 同一提问命中 ≥2 篇笔记 → proposed CO_ASKED（HITL：绝不写 confirmed）；
- confidence 随共同出现次数归一化递增；
- 与 note_relations 已有记录（任意状态/任一方向）去重；
- 增量水位：只扫描上次运行后的新提问；
- privacy_log_questions=false（question 为 NULL）安全跳过；
- dry_run 只预测不落库；
- POST /relations/mine-questions 与 GET /concept-gaps 端点契约。
"""
from __future__ import annotations

import threading
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from infrastructure.database import ProductDatabase
from application.question_concept_miner import QuestionConceptMiner


def _make_db(tmp_path) -> ProductDatabase:
    db = ProductDatabase(tmp_path / "product.sqlite3")
    db.initialize()
    return db


def _add_note(db, note_id: str, title: str, aliases: list[str] | None = None) -> None:
    import json

    fm = json.dumps({"aliases": aliases or []}, ensure_ascii=False)
    db.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, frontmatter_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (note_id, f"policies/{note_id}.md", title, f"hash-{note_id}", fm, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
    )


def _add_question(db, question: str | None, created_at: str) -> str:
    request_id = str(uuid.uuid4())
    import hashlib

    qhash = hashlib.sha256(((question or "") + "mindgraph-question-salt").encode()).hexdigest()
    db.execute(
        """INSERT INTO query_logs (
            request_id, question, question_hash, answer, result_state, requested_strategy, actual_strategy,
            trace_json, citations_json, timing_json, usage_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (request_id, question, qhash, "答", "answered", "adaptive", "dense",
         "{}", "[]", "{}", "{}", created_at),
    )
    return request_id


def _relations(db) -> list[dict]:
    return db.fetch_all("SELECT * FROM note_relations")


@pytest.fixture()
def seeded_db(tmp_path):
    db = _make_db(tmp_path)
    _add_note(db, "n1", "差旅费报销政策")
    _add_note(db, "n2", "发票管理规范")
    _add_note(db, "n3", "出差审批流程")
    return db


class TestMiningRules:
    def test_book_title_match_creates_co_asked_proposed(self, seeded_db):
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》有冲突吗？", "2026-08-20T10:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["ok"] is True
        assert result["mined"] == 1
        assert result["proposed_created"] == 1
        rows = _relations(seeded_db)
        assert len(rows) == 1
        row = rows[0]
        # HITL 红线：挖掘只允许产出 proposed
        assert row["status"] == "proposed"
        assert row["relation_type"] == "CO_ASKED"
        assert {row["source_note_id"], row["target_note_id"]} == {"n1", "n2"}
        assert row["evidence_span"] == "《差旅费报销政策》和《发票管理规范》有冲突吗？"
        assert row["evidence_section"] == "query_logs"
        assert row["extraction_method"] == "question_co_asked"
        assert row["evidence_chunk_id"] is None

    def test_unmatched_book_title_accumulates_gap(self, seeded_db):
        _add_question(seeded_db, "《餐补标准》在哪里能查到？", "2026-08-20T11:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["proposed_created"] == 0
        assert result["gap_terms"] == 1
        row = seeded_db.fetch_one("SELECT * FROM concept_signals WHERE term=?", ("餐补标准",))
        assert row is not None
        assert row["seen_count"] == 1
        assert row["sample_question_hash"]

    def test_alias_substring_match_counts_as_hit(self, seeded_db):
        seeded_db.execute("UPDATE notes SET frontmatter_json=? WHERE note_id='n3'", ('{"aliases": ["审批流"]}',))
        _add_question(seeded_db, "出差审批流和差旅费报销政策怎么衔接？", "2026-08-20T12:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["proposed_created"] == 1
        row = _relations(seeded_db)[0]
        assert {row["source_note_id"], row["target_note_id"]} == {"n1", "n3"}

    def test_confidence_grows_with_co_occurrence(self, seeded_db):
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》冲突吗？", "2026-08-20T10:00:00+00:00")
        _add_question(seeded_db, "发票管理规范与差旅费报销政策的关系是什么？", "2026-08-20T10:05:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        miner.mine(trigger="manual")

        row = _relations(seeded_db)[0]
        # 两次共同出现：0.55 + 0.05
        assert row["confidence"] == pytest.approx(0.6)

    def test_dedup_against_existing_relation_any_status_any_direction(self, seeded_db):
        # 预置一条反向 rejected 关系
        seeded_db.execute(
            """INSERT INTO note_relations (relation_id, source_note_id, target_note_id, relation_type,
               direction, status, confidence, proposed_at, evidence_span)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("seed-1", "n2", "n1", "related_to", "outgoing", "rejected", 0.5, "2026-08-19T00:00:00Z", "旧证据"),
        )
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》冲突吗？", "2026-08-20T10:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["proposed_created"] == 0
        assert result["skipped_existing"] == 1
        assert len(_relations(seeded_db)) == 1

    def test_incremental_watermark_skips_old_questions(self, seeded_db):
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》冲突吗？", "2026-08-20T10:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)
        first = miner.mine(trigger="manual")
        assert first["proposed_created"] == 1

        # 无新提问 → 不再重复挖掘
        second = miner.mine(trigger="auto")
        assert second["mined"] == 0
        assert second["reason"] == "no_new_questions"
        assert len(_relations(seeded_db)) == 1

        # 新提问带来新的缺口词 → 只增量处理
        _add_question(seeded_db, "《交通补贴》标准是什么？", "2026-08-21T10:00:00+00:00")
        third = miner.mine(trigger="auto")
        assert third["mined"] == 1
        assert third["gap_terms"] == 1
        assert seeded_db.fetch_one("SELECT seen_count AS c FROM concept_signals WHERE term='交通补贴'")["c"] == 1

    def test_gap_seen_count_accumulates_across_runs(self, seeded_db):
        miner = QuestionConceptMiner(seeded_db, gap_min_seen=2)
        _add_question(seeded_db, "《餐补标准》是什么？", "2026-08-20T10:00:00+00:00")
        miner.mine(trigger="manual")
        _add_question(seeded_db, "《餐补标准》更新了吗？", "2026-08-21T10:00:00+00:00")
        miner.mine(trigger="auto")

        row = seeded_db.fetch_one("SELECT * FROM concept_signals WHERE term=?", ("餐补标准",))
        assert row["seen_count"] == 2
        gaps = miner.top_gaps()
        assert [g["term"] for g in gaps] == ["餐补标准"]
        assert miner.gap_total() == 1

    def test_null_question_rows_are_skipped(self, seeded_db):
        # privacy_log_questions=false 时 question 为 NULL —— 绝不猜测、不报错
        _add_question(seeded_db, None, "2026-08-20T10:00:00+00:00")
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》冲突吗？", "2026-08-20T10:01:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["mined"] == 1  # 只统计到非 NULL 提问
        assert result["proposed_created"] == 1

    def test_dry_run_writes_nothing(self, seeded_db):
        _add_question(seeded_db, "《差旅费报销政策》和《发票管理规范》冲突吗？《餐补标准》呢？", "2026-08-20T10:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual", dry_run=True)

        assert result["dry_run"] is True
        assert result["proposed_created"] == 0
        assert result["gap_terms"] == 1  # 预测值
        assert _relations(seeded_db) == []
        assert seeded_db.fetch_all("SELECT * FROM concept_signals") == []
        assert seeded_db.fetch_all("SELECT * FROM concept_mine_runs") == []

        # dry_run 不推进水位：正式运行仍能挖到同一条
        real = miner.mine(trigger="manual")
        assert real["proposed_created"] == 1

    def test_single_note_question_creates_no_relation(self, seeded_db):
        _add_question(seeded_db, "《差旅费报销政策》的时限是几天？", "2026-08-20T10:00:00+00:00")
        miner = QuestionConceptMiner(seeded_db)

        result = miner.mine(trigger="manual")

        assert result["matched_questions"] == 1
        assert result["proposed_created"] == 0


@pytest.fixture(scope="module")
def api_client(tmp_path_factory):
    from unittest.mock import patch

    import api.auth as auth

    previous_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "off"
    test_database = ProductDatabase(tmp_path_factory.mktemp("miner_api") / "product.sqlite3")
    with patch("api.dependencies.ProductDatabase", return_value=test_database), \
         patch("api.dependencies.DocumentLifecycleService.import_existing_markdown"), \
         patch("api.dependencies.ServiceContainer._register_builtin_datasets"):
        from api.main import app
        with TestClient(app) as c:
            yield c, test_database
    auth.AUTH_MODE = previous_auth_mode


class TestEndpoints:
    def test_mine_questions_endpoint_contract(self, api_client):
        c, db = api_client
        _add_note(db, "n1", "差旅费报销政策")
        _add_note(db, "n2", "发票管理规范")
        _add_question(db, "《差旅费报销政策》和《发票管理规范》冲突吗？《夜班餐补》怎么算？", "2026-08-20T10:00:00+00:00")

        response = c.post("/api/v1/mindgraph/relations/mine-questions", json={"dry_run": False})

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["trigger"] == "manual"
        assert data["mined"] == 1
        assert data["proposed_created"] == 1
        assert data["gap_terms"] == 1

        row = db.fetch_one("SELECT status, relation_type FROM note_relations LIMIT 1")
        assert row["status"] == "proposed"
        assert row["relation_type"] == "CO_ASKED"

    def test_concept_gaps_endpoint_contract(self, api_client):
        c, db = api_client
        # 上一用例已写入 夜班餐补（seen_count=1）；默认阈值 2 不展示
        response = c.get("/api/v1/mindgraph/concept-gaps", params={"limit": 10})
        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"gaps", "total"}
        assert all(term in g for g in data["gaps"] for term in ("term", "seen_count", "first_seen", "last_seen"))

        # 再问一次（显式 《》 引用才累计概念信号）→ seen_count=2 → 达到阈值出现
        _add_question(db, "《夜班餐补》到底怎么算？", "2026-08-21T10:00:00+00:00")
        assert c.post("/api/v1/mindgraph/relations/mine-questions", json={}).status_code == 200
        data = c.get("/api/v1/mindgraph/concept-gaps").json()
        assert data["total"] >= 1
        assert any(g["term"] == "夜班餐补" and g["seen_count"] >= 2 for g in data["gaps"])

    def test_mine_questions_rejects_bad_limit_on_gaps(self, api_client):
        c, _ = api_client
        assert c.get("/api/v1/mindgraph/concept-gaps", params={"limit": 0}).status_code == 422
        assert c.get("/api/v1/mindgraph/concept-gaps", params={"limit": 9999}).status_code == 422


class TestAutoTrigger:
    def test_persist_invokes_callback_and_swallows_errors(self):
        from unittest.mock import patch

        from application.chat_service import ChatService

        svc = ChatService.__new__(ChatService)
        svc.on_question_logged = MagicMock(side_effect=RuntimeError("boom"))
        result = MagicMock()
        with patch.object(ChatService, "_persist_or_raise", lambda self, r: None):
            svc._persist(result)  # 回调抛错绝不影响应答路径
        svc.on_question_logged.assert_called_once()

    def test_counter_triggers_background_mine_at_threshold(self, monkeypatch):
        from api import dependencies as deps

        fake_settings = MagicMock(CONCEPT_MINE_AUTO_ENABLED=True, CONCEPT_MINE_AUTO_MIN_NEW_QUESTIONS=3)
        monkeypatch.setattr(deps, "get_settings", lambda: fake_settings)

        container = deps.ServiceContainer.__new__(deps.ServiceContainer)
        container._concept_mine_lock = threading.Lock()
        container._concept_mine_pending = 0
        container._concept_mine_running = False
        done = threading.Event()

        def fake_mine(trigger="manual", dry_run=False):
            done.set()
            return {"mined": 0, "proposed_created": 0, "gap_terms": 0}

        container.question_concept_miner = MagicMock()
        container.question_concept_miner.mine.side_effect = fake_mine

        container._maybe_auto_mine_concepts()
        container._maybe_auto_mine_concepts()
        assert container._concept_mine_pending == 2
        assert not done.is_set()  # 未达阈值不触发

        container._maybe_auto_mine_concepts()  # 第 3 条 → 后台触发
        assert done.wait(2.0)
        container.question_concept_miner.mine.assert_called_once_with(trigger="auto")
        # 等待后台线程收尾：running 复位、计数清零
        deadline = threading.Event()
        for _ in range(100):
            with container._concept_mine_lock:
                if not container._concept_mine_running:
                    break
            deadline.wait(0.02)
        assert not container._concept_mine_running
        assert container._concept_mine_pending == 0

    def test_auto_trigger_disabled_by_setting(self, monkeypatch):
        from api import dependencies as deps

        fake_settings = MagicMock(CONCEPT_MINE_AUTO_ENABLED=False)
        monkeypatch.setattr(deps, "get_settings", lambda: fake_settings)

        container = deps.ServiceContainer.__new__(deps.ServiceContainer)
        container._concept_mine_lock = threading.Lock()
        container._concept_mine_pending = 0
        container._concept_mine_running = False
        container.question_concept_miner = MagicMock()

        for _ in range(50):
            container._maybe_auto_mine_concepts()
        assert container._concept_mine_pending == 0
        container.question_concept_miner.mine.assert_not_called()
