"""
Typed HTTP client for the expense-rag-qa REST API.

Improvements over the original:
- Shared httpx.AsyncClient with connection pooling (reuse TLS sessions)
- Automatic retry on transient errors (5xx, connection resets)
- Streaming chat with keep-alive
"""

from __future__ import annotations

import json
import os
import mimetypes
import time
from typing import Any, Iterator

import httpx
import streamlit as st


# ---------------------------------------------------------------------------
# Public error
# ---------------------------------------------------------------------------
class APIClientError(RuntimeError):
    """Wraps any transport or application-level API error."""


# ---------------------------------------------------------------------------
# Retryable status codes
# ---------------------------------------------------------------------------
_RETRYABLE_CODES: frozenset = frozenset({429, 502, 503, 504})


# ---------------------------------------------------------------------------
# Shared session holder (cached per Streamlit session)
# ---------------------------------------------------------------------------
def _get_session(base_url: str, timeout: float) -> httpx.Client:
    """Return (or create) a persistent httpx.Client for the current base URL.

    Using a shared Client enables HTTP keep-alive and connection pooling,
    eliminating the TLS-handshake overhead on every API call.
    """
    session_key = f"__httpx_client_{base_url}"
    client = st.session_state.get(session_key)
    if client is None or client.is_closed:
        client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_keepalive_connections=6, max_connections=20),
        )
        st.session_state[session_key] = client
    return client


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class ProductAPIClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")).rstrip("/")
        self.timeout = timeout

    # -- internal helpers -----------------------------------------------------
    def _session(self) -> httpx.Client:
        return _get_session(self.base_url, self.timeout)

    def _request(
        self,
        method: str,
        path: str,
        retries: int = 2,
        retry_backoff: float = 0.6,
        **kwargs,
    ) -> Any:
        """Send an HTTP request with automatic retry on transient failures."""
        last_exc: Exception | None = None
        timeout = kwargs.pop("timeout", self.timeout)

        for attempt in range(retries + 1):
            try:
                response = self._session().request(method, path, timeout=timeout, **kwargs)
                response.raise_for_status()
                if response.headers.get("content-type", "").startswith("application/json"):
                    return response.json()
                return response.text
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_CODES and attempt < retries:
                    time.sleep(retry_backoff * (2**attempt))
                    last_exc = exc
                    continue
                try:
                    detail = exc.response.json()
                except Exception:
                    detail = {}
                message = detail.get("detail", detail.get("message", "API request failed"))
                raise APIClientError(message) from exc
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
                if attempt < retries:
                    time.sleep(retry_backoff * (2**attempt))
                    last_exc = exc
                    continue
                raise APIClientError("后端服务不可用，请确认 FastAPI 已在运行并刷新页面。") from exc
            except httpx.HTTPError as exc:
                raise APIClientError("后端服务不可用，请确认 FastAPI 已在运行并刷新页面。") from exc

        raise APIClientError("请求失败（已达最大重试次数）") from last_exc

    # -- health ---------------------------------------------------------------
    def health(self):        return self._request("GET", "/health")
    def readiness(self):     return self._request("GET", "/readiness")
    def public_config(self): return self._request("GET", "/config/public")

    # -- chat -----------------------------------------------------------------
    def chat(self, payload): return self._request("POST", "/chat", json=payload)

    def stream_chat(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Stream SSE events from /chat/stream with automatic reconnection."""
        try:
            with self._session().stream(
                "POST",
                "/chat/stream",
                json=payload,
                timeout=httpx.Timeout(self.timeout, read=120.0),
            ) as response:
                response.raise_for_status()
                event_name = None
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        try:
                            item = json.loads(line.split(":", 1)[1].strip())
                        except (json.JSONDecodeError, ValueError) as exc:
                            raise APIClientError("SSE 协议异常，请刷新页面重试。") from exc
                        if event_name and item.get("event") != event_name:
                            event_name = None  # 重置后继续，不中止
                        yield item
                    else:
                        event_name = None  # 空行/注释行，重置 event_name
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
            raise APIClientError("流式连接中断，请重新发送问题。") from exc
        except httpx.HTTPError as exc:
            raise APIClientError("流式连接失败，请确认后端服务正在运行。") from exc

    # -- knowledge ------------------------------------------------------------
    def documents(self):  return self._request("GET", "/knowledge/documents")
    def index_status(self): return self._request("GET", "/knowledge/index/status")
    def rebuild_index(self): return self._request("POST", "/knowledge/index/rebuild", timeout=300.0)
    def delete_document(self, document_id): return self._request("DELETE", f"/knowledge/documents/{document_id}")
    def upload_document(self, name, content):
        return self._request(
            "POST", "/knowledge/documents",
            files={"file": (name, content, "text/markdown")},
            data={"category": "upload"},
        )

    def document_versions(self, status=None, category=None):
        return self._request(
            "GET", "/knowledge/versions",
            params={k: v for k, v in {"status": status, "category": category}.items() if v},
        )

    def upload_document_version(self, name, content, metadata):
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        clean = {k: v for k, v in metadata.items() if v not in (None, "")}
        return self._request(
            "POST", "/knowledge/versions",
            files={"file": (name, content, media_type)},
            data=clean,
            timeout=120.0,
        )

    def transition_document(self, document_id, target):
        return self._request("POST", f"/knowledge/versions/{document_id}/transition", params={"target": target})

    def incremental_rebuild(self):
        return self._request("POST", "/knowledge/index/incremental-rebuild", timeout=600.0)

    def index_versions(self):
        return self._request("GET", "/knowledge/index/versions")

    def activate_index(self, version, reason):
        return self._request("POST", f"/knowledge/index/versions/{version}/activate", params={"reason": reason})

    def rollback_index(self, reason):
        return self._request("POST", "/knowledge/index/rollback", params={"reason": reason})

    # -- feedback & governance ------------------------------------------------
    def create_feedback(self, payload):   return self._request("POST", "/feedback", json=payload)
    def bad_cases(self, status=None, category=None):
        return self._request("GET", "/bad-cases", params={k: v for k, v in {"status": status, "category": category}.items() if v})
    def update_bad_case(self, bad_case_id, payload):
        return self._request("PATCH", f"/bad-cases/{bad_case_id}", json=payload)
    def export_bad_cases(self, status=None, category=None):
        return self._request("GET", "/bad-cases/export", params={k: v for k, v in {"status": status, "category": category}.items() if v})

    # -- evaluations ----------------------------------------------------------
    def evaluation_runs(self):   return self._request("GET", "/evaluations/runs")
    def evaluation_run(self, run_id): return self._request("GET", f"/evaluations/runs/{run_id}")
    def create_evaluation(self, payload):
        return self._request("POST", "/evaluations/runs", json=payload, timeout=300.0)

    def datasets(self):   return self._request("GET", "/governance/datasets")
    def prompts(self):    return self._request("GET", "/governance/prompts")
    def human_reviews(self, run_id): return self._request("GET", f"/governance/human-reviews/{run_id}")
    def create_human_review(self, payload):
        return self._request("POST", "/governance/human-reviews", json=payload)
