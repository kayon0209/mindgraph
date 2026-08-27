"""Real Keycloak JWK rotation E2E (requires Docker Desktop engine running).

流程：起 Keycloak 容器 → admin API 建 realm+client → client credentials 签真实 token
→ MindGraph oidc.validate_id_token 通过 → Keycloak 轮换 realm keys → 旧 token 失效、
新 token 通过。

前置：Docker Desktop 引擎已启动。未启动时本脚本给出明确提示并退出。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import jwt

DOCKER = r"C:\Users\Rose\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
BASE = "http://127.0.0.1:8080"
REALM = "mindgraph-e2e"


def docker(args: list[str]) -> str:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True).stdout.strip()


def wait_ready(timeout: int = 180) -> None:
    # Keycloak 就绪信号：master realm 的 OIDC discovery 可达
    url = f"{BASE}/realms/master/.well-known/openid-configuration"
    start = time.time()
    last = ""
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except Exception as exc:  # 启动期连接重置/拒绝均属正常，继续重试
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(3)
    raise SystemExit(f"Keycloak did not become ready in time (last: {last})")


def main() -> int:
    # ── 0) 前置检查：Docker 引擎 ──
    if docker(["info"]) == "":
        print("ERROR: Docker Desktop engine is not running. Please start Docker Desktop and retry.")
        return 2

    # ── 1) 起 Keycloak ──
    root = Path(__file__).resolve().parents[1]
    print("starting keycloak container...")
    subprocess.run(
        [DOCKER, "compose", "-f", str(root / "docker-compose.keycloak.yml"), "up", "-d"],
        check=False,
    )
    wait_ready()
    print(f"keycloak ready at {BASE}/realms/{REALM}")

    # ── 2) admin token（master realm，admin:admin） ──
    with httpx.Client(timeout=20.0) as c:
        r = c.post(
            f"{BASE}/realms/master/protocol/openid-connect/token",
            data={"grant_type": "password", "client_id": "admin-cli",
                  "username": "admin", "password": "admin"},
        )
        r.raise_for_status()
        admin_token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # ── 3) 建 realm（存在则忽略） ──
        r = c.post(f"{BASE}/admin/realms", headers=headers, json={
            "realm": REALM, "enabled": True, "sslRequired": "none",
        })
        if r.status_code not in (201, 409):
            raise RuntimeError(f"create realm failed: {r.status_code} {r.text[:300]}")

        # ── 4) 建 client（service account） ──
        r = c.post(f"{BASE}/admin/realms/{REALM}/clients", headers=headers, json={
            "clientId": "mg-e2e", "enabled": True, "publicClient": False,
            "serviceAccountsEnabled": True, "standardFlowEnabled": False,
        })
        if r.status_code not in (201, 409):  # 409=已存在，幂等继续
            r.raise_for_status()
        client_uuid = next(
            x["id"] for x in c.get(f"{BASE}/admin/realms/{REALM}/clients",
                                   headers=headers).json() if x["clientId"] == "mg-e2e"
        )
        secret = c.get(f"{BASE}/admin/realms/{REALM}/clients/{client_uuid}/client-secret",
                       headers=headers).json()["value"]

        # ── 5) 签真实 token（client credentials） ──
        def get_token() -> str:
            return c.post(
                f"{BASE}/realms/{REALM}/protocol/openid-connect/token",
                data={"grant_type": "client_credentials", "client_id": "mg-e2e",
                      "client_secret": secret},
            ).json()["access_token"]

        # ── 6) MindGraph 真实校验 ──
        os.environ["OIDC_ENABLED"] = "true"
        os.environ["OIDC_ISSUER_URL"] = f"{BASE}/realms/{REALM}"
        os.environ["OIDC_CLIENT_ID"] = "mg-e2e"
        os.environ["OIDC_AUDIENCE"] = "account"
        os.environ["OIDC_ALGORITHMS"] = "RS256"
        sys.path.insert(0, str(root / "src"))
        from api import oidc

        token_before = get_token()
        claims_before = oidc.validate_id_token(token_before)
        ok_before = claims_before is not None
        print(f"[{'PASS' if ok_before else 'FAIL'}] pre-rotation real Keycloak token valid | sub={claims_before.get('sub') if claims_before else None}")

        # ── 7) 轮换 realm keys（components API：新 provider 更高 priority → 删旧 provider） ──
        providers = c.get(
            f"{BASE}/admin/realms/{REALM}/components?type=org.keycloak.keys.KeyProvider",
            headers=headers,
        ).json()
        old_provider = next(p for p in providers if p["providerId"] == "rsa-generated")
        old_id = old_provider["id"]
        old_prio = int((old_provider["config"].get("priority") or ["100"])[0])
        print(f"  existing provider: {old_provider['name']} id={old_id} priority={old_prio}")

        new_priority = str(old_prio + 100)
        r = c.post(f"{BASE}/admin/realms/{REALM}/components", headers=headers, json={
            "name": "rsa-generated-rotated",
            "providerId": "rsa-generated",
            "providerType": "org.keycloak.keys.KeyProvider",
            "config": {"priority": [new_priority], "enabled": ["true"], "active": ["true"],
                       "keySize": ["2048"], "algorithm": ["RS256"]},
        })
        r.raise_for_status()
        print(f"  created rotated provider priority={new_priority}")

        # 新签 token 的 kid 应已变化（active key 切换）
        token_after = get_token()
        kid_old = jwt.get_unverified_header(token_before).get("kid")
        kid_new = jwt.get_unverified_header(token_after).get("kid")
        ok_kid_changed = kid_new != kid_old
        print(f"[{'PASS' if ok_kid_changed else 'FAIL'}] active signing key rotated (kid) | {kid_old} -> {kid_new}")

        # 删除旧 provider → 旧 key 从 JWKS 移除
        r = c.delete(f"{BASE}/admin/realms/{REALM}/components/{old_id}", headers=headers)
        r.raise_for_status()
        print("  deleted old key provider")
        time.sleep(2)
        oidc._get_jwks_client(force=True)

        # 旧 token 应失效
        claims_old_after = oidc.validate_id_token(token_before)
        ok_old_invalid = claims_old_after is None
        print(f"[{'PASS' if ok_old_invalid else 'FAIL'}] post-rotation old token INVALID | validate={claims_old_after is not None}")

        # 新 token 应有效
        claims_after = oidc.validate_id_token(token_after)
        ok_after = claims_after is not None
        print(f"[{'PASS' if ok_after else 'FAIL'}] post-rotation new token valid | sub={claims_after.get('sub') if claims_after else None}")

    all_ok = ok_before and ok_kid_changed and ok_old_invalid and ok_after
    print("RESULT:", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
