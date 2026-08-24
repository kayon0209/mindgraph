from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest

from api import oidc
from infrastructure.settings import get_settings


@pytest.fixture(autouse=True)
def restore_jwks_cache():
    snapshot = dict(oidc._JWKS_CACHE)
    yield
    oidc._JWKS_CACHE.clear()
    oidc._JWKS_CACHE.update(snapshot)


def test_validation_uses_discovered_jwks_uri_and_reuses_client(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "OIDC_ENABLED", True)
    monkeypatch.setattr(settings, "OIDC_ISSUER_URL", "https://issuer.example")
    monkeypatch.setattr(settings, "OIDC_CLIENT_ID", "mindgraph")
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", "mindgraph")
    monkeypatch.setattr(settings, "OIDC_ALGORITHMS", "RS256")
    monkeypatch.setattr(settings, "OIDC_JWKS_CACHE_TTL_SECONDS", 600)
    oidc._JWKS_CACHE.update(
        {"issuer": None, "jwks_uri": None, "client": None, "fetched_at": 0.0}
    )

    discovery_urls: list[str] = []
    constructed_urls: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"jwks_uri": "https://keys.example/custom/jwks"}

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str):
            discovery_urls.append(url)
            return Response()

    class JwksClient:
        def __init__(self, url: str, **_kwargs):
            constructed_urls.append(url)

        def get_signing_key_from_jwt(self, _token: str):
            return SimpleNamespace(key="public-key")

    monkeypatch.setattr(oidc.httpx, "Client", HttpClient)
    monkeypatch.setattr(jwt, "PyJWKClient", JwksClient)
    monkeypatch.setattr(jwt, "decode", lambda *_args, **_kwargs: {"sub": "user-1"})

    assert oidc.validate_id_token("token-1") == {"sub": "user-1"}
    assert oidc.validate_id_token("token-2") == {"sub": "user-1"}
    assert discovery_urls == [
        "https://issuer.example/.well-known/openid-configuration"
    ]
    assert constructed_urls == ["https://keys.example/custom/jwks"]
