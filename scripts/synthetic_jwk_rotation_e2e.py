"""Synthetic JWK rotation E2E — zero Docker, real MindGraph OIDC code.

设计（对齐 oidc.py 的验证路径）：
- 本地假 IdP（http.server 标准库）：/.well-known/openid-configuration + /jwks
- RSA 密钥对 A 签发 token → oidc.validate_id_token 通过
- 轮换到密钥对 B（JWKS 只含 B）→ 强制刷新 JWKS 缓存后，旧 token(A) 必须失败、新 token(B) 必须通过
- 同时记录"未刷新缓存的轮换语义"（OIDC 标准允许旧 key 在缓存 TTL 内过渡）

诚信口径：合成数据验证（非真实企业 IdP / 非生产环境）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── 让 src/ 可导入（oidc.py 以 src 为根：from infrastructure.settings import ...） ──
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# OIDC 配置必须在 get_settings() 首次调用前就位（pydantic-settings 缓存）
os.environ["OIDC_ENABLED"] = "true"
os.environ["OIDC_AUDIENCE"] = "synthetic-aud"
os.environ["OIDC_ALGORITHMS"] = "RS256"
os.environ["OIDC_JWKS_CACHE_TTL_SECONDS"] = "1"

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import logging  # noqa: E402
logging.getLogger("mindgraph.auth.oidc").setLevel(logging.CRITICAL)  # 压掉预期失败的堆栈噪音

from api import oidc  # noqa: E402


# ── 工具：RSA 密钥对 → JWK ──
def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def gen_keypair(prefix: str) -> tuple:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key()
    kid = f"{prefix}-{time.time_ns()}"
    nums = pub.public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": b64u(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")),
        "e": b64u(nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")),
    }
    return key, kid, jwk


def make_token(private_key, kid: str, issuer: str, aud: str, sub: str = "u1") -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": aud,
        "exp": now + 300,
        "iat": now,
        "sub": sub,
        "preferred_username": "alice",
        "roles": ["admin"],
        "workspaces": ["ws-a"],
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


# ── 本地假 IdP ──
class IdPState:
    def __init__(self) -> None:
        self.keys: list[dict] = []


state = IdPState()


class IdPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # 静默
        pass

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/.well-known/openid-configuration"):
            issuer = f"http://127.0.0.1:{self.server.server_address[1]}"
            self._send({"issuer": issuer, "jwks_uri": f"{issuer}/jwks"})
        elif self.path.startswith("/jwks"):
            self._send({"keys": state.keys})
        else:
            self.send_error(404)


# ── 主流程 ──
def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), IdPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_address[1]}"
    # ISSUER_URL 依赖动态端口，须在 server 启动后、get_settings() 首次调用前设置
    os.environ["OIDC_ISSUER_URL"] = issuer
    aud = os.environ["OIDC_AUDIENCE"]

    results: list[tuple[str, bool, str]] = []

    # 1) 密钥对 A：签发 + 验证（轮换前）
    key_a, kid_a, jwk_a = gen_keypair("a")
    state.keys = [jwk_a]  # 初始 JWKS 只暴露 A
    token_a = make_token(key_a, kid_a, issuer, aud)
    claims_a = oidc.validate_id_token(token_a)
    ok1 = claims_a is not None and claims_a.get("sub") == "u1"
    results.append(("pre-rotation token A valid", ok1,
                    f"sub={claims_a.get('sub') if claims_a else None}"))

    # 2) 密钥对 B：签发（轮换后使用）
    key_b, kid_b, jwk_b = gen_keypair("b")
    token_b = make_token(key_b, kid_b, issuer, aud)

    # 3) 轮换：JWKS 只暴露 B
    state.keys = [jwk_b]

    # 4) 未强制刷新缓存时：旧 token A 仍可验证（缓存 TTL 内过渡，OIDC 预期行为）
    claims_a_cached = oidc.validate_id_token(token_a)
    results.append(("post-rotation token A (cached, no refresh) still valid",
                    claims_a_cached is not None,
                    "expected: True (JWKS cache grace period)"))

    # 5) 强制刷新 JWKS 缓存（等价 TTL 到期 / client 重建）
    oidc._get_jwks_client(force=True)
    claims_a_after = oidc.validate_id_token(token_a)
    ok3 = claims_a_after is None
    results.append(("post-refresh token A INVALID (old key rotated out)",
                    ok3, f"validate={claims_a_after}"))

    # 6) 新密钥 B 的 token 通过
    claims_b = oidc.validate_id_token(token_b)
    ok4 = claims_b is not None and claims_b.get("sub") == "u1"
    results.append(("post-rotation token B valid (new key active)", ok4,
                    f"sub={claims_b.get('sub') if claims_b else None}"))

    # 7) principal 映射
    principal = oidc.claims_to_principal(claims_b) if claims_b else {}
    ok5 = principal.get("auth_mode") == "oidc" and "admin" in principal.get("roles", [])
    results.append(("claims_to_principal maps roles/name", ok5,
                    f"roles={principal.get('roles')} name={principal.get('name')}"))

    server.shutdown()
    server.server_close()

    print("=" * 68)
    print("SYNTHETIC JWK ROTATION E2E (real oidc.validate_id_token)")
    print(f"issuer={issuer}  aud={aud}  RS256")
    print("=" * 68)
    all_ok = True
    for name, ok, detail in results:
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name} | {detail}")
    print("-" * 68)
    print("RESULT:", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
