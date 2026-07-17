"""本地员工账号与会话管理。"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config import AVATAR_DIR, EMPLOYEES_FILE, SESSIONS_FILE, USERS_FILE

DEFAULT_EMPLOYEES = [
    {
        "employee_id": "E00001",
        "real_name": "内部员工",
        "department": "财务部",
        "title": "员工",
        "email": "employee@example.internal",
    }
]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_employee_roster() -> None:
    if not EMPLOYEES_FILE.exists():
        _write_json(EMPLOYEES_FILE, DEFAULT_EMPLOYEES)


def load_employees() -> list[Dict[str, Any]]:
    ensure_employee_roster()
    employees = _read_json(EMPLOYEES_FILE, [])
    return employees if isinstance(employees, list) else []


def load_users() -> Dict[str, Dict[str, Any]]:
    users = _read_json(USERS_FILE, {})
    return users if isinstance(users, dict) else {}


def save_users(users: Dict[str, Dict[str, Any]]) -> None:
    _write_json(USERS_FILE, users)


def find_employee(employee_id: str, real_name: str) -> Optional[Dict[str, Any]]:
    employee_id = employee_id.strip()
    real_name = real_name.strip()
    for employee in load_employees():
        if (
            str(employee.get("employee_id", "")).strip() == employee_id
            and str(employee.get("real_name", "")).strip() == real_name
        ):
            return employee
    return None


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return salt, base64.b64encode(digest).decode("ascii")


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, digest = _hash_password(password, salt)
    return secrets.compare_digest(digest, expected_hash)


def register_user(employee_id: str, real_name: str, password: str) -> tuple[bool, str]:
    employee = find_employee(employee_id, real_name)
    if employee is None:
        return False, "员工号与真实姓名未匹配到内部员工名册。"
    if len(password) < 8:
        return False, "密码至少需要 8 位。"

    users = load_users()
    if employee_id in users:
        return False, "该员工号已注册，请直接登录。"

    salt, password_hash = _hash_password(password)
    users[employee_id] = {
        "employee_id": employee_id,
        "real_name": employee["real_name"],
        "nickname": employee["real_name"],
        "department": employee.get("department", ""),
        "title": employee.get("title", ""),
        "email": employee.get("email", ""),
        "password_salt": salt,
        "password_hash": password_hash,
        "avatar_path": "",
        "created_at": int(time.time()),
    }
    save_users(users)
    return True, "注册成功，请登录。"


def ensure_demo_user() -> Dict[str, Any]:
    ensure_employee_roster()
    users = load_users()
    if "E00001" not in users:
        register_user("E00001", "内部员工", "Internal@123")
        users = load_users()
    return public_user(users["E00001"])


def authenticate(employee_id: str, password: str) -> Optional[Dict[str, Any]]:
    user = load_users().get(employee_id.strip())
    if not user:
        return None
    if not verify_password(password, user.get("password_salt", ""), user.get("password_hash", "")):
        return None
    return public_user(user)


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in user.items()
        if key not in {"password_salt", "password_hash"}
    }


def get_user(employee_id: str) -> Optional[Dict[str, Any]]:
    user = load_users().get(employee_id)
    return public_user(user) if user else None


def create_session(employee_id: str) -> str:
    token = secrets.token_urlsafe(32)
    sessions = _read_json(SESSIONS_FILE, {})
    if not isinstance(sessions, dict):
        sessions = {}
    sessions[token] = {"employee_id": employee_id, "created_at": int(time.time())}
    _write_json(SESSIONS_FILE, sessions)
    return token


def get_session_user(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    sessions = _read_json(SESSIONS_FILE, {})
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(token)
    if not session:
        return None
    # 检查会话是否过期（默认 1 小时）
    created_at = session.get("created_at", 0)
    timeout = int(os.getenv("SESSION_TIMEOUT_SECONDS", "3600"))
    if int(time.time()) - created_at > timeout:
        delete_session(token)
        return None
    return get_user(str(session.get("employee_id", "")))


def delete_session(token: str) -> None:
    if not token:
        return
    sessions = _read_json(SESSIONS_FILE, {})
    if not isinstance(sessions, dict):
        return
    if token in sessions:
        del sessions[token]
        _write_json(SESSIONS_FILE, sessions)


def save_avatar(employee_id: str, filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    path = AVATAR_DIR / f"{employee_id}{suffix}"
    path.write_bytes(data)

    users = load_users()
    if employee_id in users:
        users[employee_id]["avatar_path"] = str(path)
        # 保留用户自定义昵称，不覆盖
        save_users(users)
    return str(path)
