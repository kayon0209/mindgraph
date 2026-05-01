"""项目路径与环境变量（从项目根目录 `.env` 加载）。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ZHIPU_API_KEY: str = (os.getenv("ZHIPU_API_KEY") or "").strip()
AUTH_MODE: str = (os.getenv("AUTH_MODE") or "demo").strip().lower()

if not ZHIPU_API_KEY:
    import warnings
    warnings.warn(
        "未检测到 ZHIPU_API_KEY。请在项目根目录创建 `.env` 文件并设置：\n"
        "  ZHIPU_API_KEY=你的密钥\n"
        "申请地址：https://open.bigmodel.cn/",
        RuntimeWarning,
        stacklevel=2,
    )

CHROMA_DIR = ROOT / "data" / "chroma"
DOCS_DIR = ROOT / "knowledge"  # 知识库文档目录
# PRD v1：用户上传的制度 Markdown（与 docs/ 一并入库）
UPLOAD_DIR = ROOT / "data" / "uploads"
USERS_FILE = ROOT / "data" / "users.json"
EMPLOYEES_FILE = ROOT / "data" / "employees.json"
AVATAR_DIR = ROOT / "data" / "avatars"
SESSIONS_FILE = ROOT / "data" / "sessions.json"

COLLECTION_NAME = "expense_kb_v2"
CHAT_MODEL = "glm-4.5-air"
EMBED_MODEL = "embedding-3"

# 检索与生成（与 PRD-v1 对齐：Top-K=3，余弦距离阈值 0.5）
DEFAULT_TOP_K = 3
SIMILARITY_THRESHOLD = 0.5
MAX_CONTEXT_CHARS = 6000
