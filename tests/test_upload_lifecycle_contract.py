"""前端上传链路的**契约测试**（P4）。

为什么需要它：``web/src/lib/api.ts`` 的 ``uploadDocumentVersion`` 按四步走——
``POST /knowledge/versions`` → ``transition?target=pending_index`` →
``transition?target=active`` → ``POST /index/incremental-rebuild``。前端的单测
全是 mock 的，路径或字段名一变，它照样绿，只有真机点一次才会炸。这里用真实
app + 真实 ``DocumentLifecycleService`` 把这套契约钉住。

同时钉住两件容易踩的事：
1. 中文文件名不能直接当 ``logical_document_id``——它会被拼进存储路径，后端
   ``_SAFE_SEGMENT`` 只接受 ASCII（前端因此做了 slug），这里把拒绝行为写死；
2. 材料要真的可检索：走完流转后必须出现在 ``active_chunks`` 里——否则
   "上传成功"只是文件躺在磁盘上。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from application.document_lifecycle_service import DocumentLifecycleService
from infrastructure.database import ProductDatabase

PREFIX = "/api/v1"


@pytest.fixture
def flow(tmp_path: Path):
    """真实文档生命周期 + 记录式索引服务（索引构建依赖 BGE，这里只验参数透传）。"""
    db = ProductDatabase(tmp_path / "flow.sqlite3")
    db.initialize()
    lifecycle = DocumentLifecycleService(db, tmp_path / "documents")
    build_calls: list[bool] = []

    class _RecordingIndex:
        def build(self, *, force: bool = False):
            build_calls.append(force)
            return {"index_version": "m4-test", "forced": force}

    import api.dependencies as deps
    from api.main import register_exception_handlers
    from api.routes import knowledge as knowledge_route

    original = deps._override
    deps._override = SimpleNamespace(
        database=db, document_lifecycle=lifecycle, index_lifecycle=_RecordingIndex()
    )
    app = FastAPI()
    # 不装统一异常处理器的话，409/422 会退化成 500——那样测的就不是线上行为
    register_exception_handlers(app)
    app.include_router(knowledge_route.router, prefix=PREFIX)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(client=client, db=db, lifecycle=lifecycle, build_calls=build_calls)
    finally:
        client.close()
        deps._override = original


def test_upload_flow_reaches_active_and_is_searchable(flow) -> None:
    """四步链走完，材料真的可检索（这是"上传成功"的唯一硬定义）。"""
    uploaded = flow.client.post(
        f"{PREFIX}/knowledge/versions",
        files={"file": ("travel-policy.md", "# 差旅费\n十个工作日内报销。".encode(), "text/markdown")},
        data={
            "logical_document_id": "travel-policy",
            "version": "v1",
            "category": "upload",
            "authority_level": "user_uploaded_reference",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    record = uploaded.json()
    assert record["status"] == "draft", "新上传默认 draft——前端必须显式流转"
    assert record["logical_document_id"] == "travel-policy"

    for target in ("pending_index", "active"):
        moved = flow.client.post(
            f"{PREFIX}/knowledge/versions/{record['document_id']}/transition",
            params={"target": target},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == target

    rebuilt = flow.client.post(f"{PREFIX}/knowledge/index/incremental-rebuild")
    assert rebuilt.status_code == 200
    assert flow.build_calls == [False], "默认不带 force（逃生口必须显式）"

    searchable = " ".join(str(item["text"]) for item in flow.lifecycle.active_chunks())
    assert "十个工作日内报销" in searchable, "走完流转仍不可检索 = 这份材料根本没进索引"


def test_draft_cannot_jump_straight_to_active(flow) -> None:
    """draft→active 一跳被拒：前端必须走两跳（少一跳就是静默进不了索引）。"""
    uploaded = flow.client.post(
        f"{PREFIX}/knowledge/versions",
        files={"file": ("policy.md", "# 制度\n内容".encode(), "text/markdown")},
        data={"logical_document_id": "policy", "version": "v1", "category": "upload"},
    )
    document_id = uploaded.json()["document_id"]

    jumped = flow.client.post(f"{PREFIX}/knowledge/versions/{document_id}/transition", params={"target": "active"})
    assert jumped.status_code == 409, "状态机不允许跳级——这条断言保护的是前端的两次流转"


def test_chinese_logical_id_is_rejected_and_slug_works(flow) -> None:
    """中文名不能直接当逻辑文档 id（会被拼进路径），前端 slug 后的 ASCII 值可用。"""
    rejected = flow.client.post(
        f"{PREFIX}/knowledge/versions",
        files={"file": ("政策.md", "# 制度\n内容".encode(), "text/markdown")},
        data={"logical_document_id": "差旅费管理办法", "version": "v1", "category": "upload"},
    )
    assert rejected.status_code == 422, "中文 logical_document_id 必须被 _SAFE_SEGMENT 挡回"

    # 前端 slugifyLogicalId("差旅费管理办法.md") 的产物形态：doc-<8 位摘要>
    accepted = flow.client.post(
        f"{PREFIX}/knowledge/versions",
        files={"file": ("政策.md", "# 制度\n内容".encode(), "text/markdown")},
        data={"logical_document_id": "doc-1a2b3c4d", "version": "v1", "category": "upload"},
    )
    assert accepted.status_code == 201, accepted.text


def test_same_name_same_version_conflicts_but_content_version_succeeds(flow) -> None:
    """同名同版本冲突 → 内容派生版本可用（前端据此自动重试一次，而不是死路）。"""
    def upload(version: str, body: str):
        return flow.client.post(
            f"{PREFIX}/knowledge/versions",
            files={"file": ("policy.md", body.encode(), "text/markdown")},
            data={"logical_document_id": "policy", "version": version, "category": "upload"},
        )

    assert upload("v1", "# 第一版\n内容").status_code == 201
    conflict = upload("v1", "# 第二版\n内容")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"  # 错误体形状：前端 describeApiError 依赖它
    assert upload("u20260912T103000s12", "# 第二版\n内容").status_code == 201
