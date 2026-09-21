"""项目路径与环境变量（从项目根目录 `.env` 加载）。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ── 环境要求：足够的提交内存（历史故障记录，2026-09-20/21）─────────────
# 这里**刻意不设** OMP_NUM_THREADS / HF_DEACTIVATE_ASYNC_LOAD。
#
# 曾经设过（OMP=1 + 关异步加载），当时观察到"先载入 FAISS 索引与 BGE、再加载
# CrossEncoder 精排器"会确定性段错误，并误判为 OpenMP 运行时刻冲突。真正的
# 原因是**环境提交内存不足**：页面文件只有 12.5GB（系统盘自动管理）、C 盘仅剩
# 3.6GB，加载 1.1GB 权重时贴着上限。
#
# 2026-09-21 把页面文件改为 C 2048MB + D 16384MB（共 18GB）后复测，**这些限制
# 全部不再必要**，各种组合都稳定：
#   OMP=12 + 关异步 → 4/4     OMP=4 + 关异步 → 4/4     OMP=1 + 关异步 → 4/4
#   OMP=12 + 不关异步 → 5/5   OMP=1 + 不关异步 → 5/5
# 而 OMP=1 会让 BGE 编码退化成单线程（查询延迟 ~38ms → ~100ms 量级）——用
# 真实性能换一个并不存在的故障不划算，所以移除。
#
# 该故障长什么样（仅供排查，环境正常时不会出现）：
#   - 进程 exit 139 / access violation，Python 层无 traceback，日志停在加载模型处
#   - OSError 1455「页面文件太小」，抛在 safetensors 的 safe_open
#   - bash 侧 fork: Resource temporarily unavailable + 0xC000012D(STATUS_COMMITMENT_LIMIT)
# 症状出现时请检查页面文件大小与 C 盘可用空间，**不要去调 OMP 线程数**。

ZHIPU_API_KEY: str = (os.getenv("ZHIPU_API_KEY") or "").strip()
ZHIPU_MODEL: str = (os.getenv("ZHIPU_MODEL") or "glm-4.7").strip()
AUTH_MODE: str = (os.getenv("AUTH_MODE") or "demo").strip().lower()
CHAT_PROVIDER: str = (os.getenv("CHAT_PROVIDER") or "zhipu").strip().lower()

if CHAT_PROVIDER == "zhipu" and not ZHIPU_API_KEY:
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
CHAT_MODEL = ZHIPU_MODEL
EMBED_MODEL = "embedding-3"

# 检索与生成（与 PRD-v1 对齐：Top-K=3，余弦距离阈值 0.5）
DEFAULT_TOP_K = 3
SIMILARITY_THRESHOLD = 0.5
MAX_CONTEXT_CHARS = 6000
