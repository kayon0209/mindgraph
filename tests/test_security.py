"""安全模块的单元测试：PII检测、脱敏、输入清理、文件名验证。"""
from __future__ import annotations

import pytest

from infrastructure.security import (
    detect_pii,
    has_injection_risk,
    hash_content,
    redact_pii,
    sanitize_input,
    validate_content_type,
    validate_filename,
)


class TestPIIDetection:
    def test_detect_chinese_id(self):
        findings = detect_pii("我的身份证号是110101199001011234")
        assert "cn_id" in findings
        assert len(findings["cn_id"]) > 0

    def test_detect_phone(self):
        findings = detect_pii("联系电话：13800138000")
        assert "cn_phone" in findings

    def test_detect_email(self):
        findings = detect_pii("邮箱 test@example.com 有问题")
        assert "email" in findings

    def test_no_pii_in_clean_text(self):
        findings = detect_pii("差旅费报销时限为10个工作日")
        assert len(findings) == 0


class TestPIIRedaction:
    def test_redact_id_card(self):
        redacted = redact_pii("身份证110101199001011234请提供")
        assert "[身份证号已隐藏]" in redacted
        assert "110101" not in redacted

    def test_redact_multiple_types(self):
        original = "姓名张三，电话13800138000，邮箱a@b.com"
        redacted = redact_pii(original)
        assert "[手机号已隐藏]" in redacted
        assert "[邮箱已隐藏]" in redacted


class TestSanitizeInput:
    def test_strips_whitespace(self):
        assert sanitize_input("   hello   ") == "hello"

    def test_empty_input(self):
        assert sanitize_input("") == ""
        assert sanitize_input(None) == ""

    def test_truncates_long_input(self):
        long_text = "A" * 3000
        result = sanitize_input(long_text)
        assert len(result) <= 2000 + 3  # +3 for "..."

    def test_preserves_normal_text(self):
        text = "差旅费报销时限是多少天？"
        assert sanitize_input(text) == text


class TestInjectionDetection:
    def test_sql_injection_detected(self):
        has_risk, risks = has_injection_risk("DROP TABLE users;")
        assert has_risk

    def test_xss_detected(self):
        has_risk, risks = has_injection_risk("<script>alert(1)</script>")
        assert has_risk

    def test_safe_query(self):
        has_risk, risks = has_injection_risk("出差住宿标准是多少？")
        assert not has_risk


class TestFilenameValidation:
    def test_valid_md_filename(self):
        assert validate_filename("policy.md") == "policy.md"

    def test_rejects_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            validate_filename("malware.exe")

    def test_strips_path_traversal(self):
        safe = validate_filename("../../etc/passwd")
        assert "passwd" in safe
        assert "../" not in safe

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_filename("")


class TestContentTypeValidation:
    def test_allows_markdown(self):
        assert validate_content_type("text/markdown", "test.md")

    def test_allows_pdf(self):
        assert validate_content_type("application/pdf", "test.pdf")

    def test_allows_octet_stream_fallback(self):
        assert validate_content_type("application/octet-stream", "test.md")

    def test_rejects_exe(self):
        assert not validate_content_type("application/x-msdownload", "test.exe")


class TestHash:
    def test_hash_is_deterministic(self):
        content = b"hello world"
        assert hash_content(content) == hash_content(content)

    def test_hash_is_sha256_length(self):
        h = hash_content(b"test")
        assert len(h) == 64


def test_watchdog_times_out_stuck_endpoint():
    """P1-P1：全局软看门狗——卡死端点 30s 内返回 504，不再无限挂起
    （走查发现：进程可因 SQLite 锁活着但不响应，拖垮全站连接池）。"""
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.middleware import WatchdogMiddleware

    mini = FastAPI()
    mini.add_middleware(WatchdogMiddleware, timeout_seconds=0.3)

    @mini.get("/stuck")
    async def stuck():
        await asyncio.sleep(10)
        return {"ok": True}

    @mini.get("/fast")
    async def fast():
        return {"ok": True}

    @mini.post("/mindgraph/chat/stream")
    async def stream_exempt():
        await asyncio.sleep(0.5)
        return {"ok": True}

    client = TestClient(mini, raise_server_exceptions=False)
    try:
        resp = client.get("/fast")
        assert resp.status_code == 200
        resp = client.get("/stuck")
        assert resp.status_code == 504
        assert resp.json()["error"]["code"] == "watchdog_timeout"
        # SSE 豁免：stream 路径不受超时影响
        resp = client.post("/mindgraph/chat/stream")
        assert resp.status_code == 200
    finally:
        client.close()
