"""
Streamlit 入口：企业报销知识问答 Web 界面。
API Key 仅在项目根目录 `.env` 中配置（见 `config.py`），不在页面展示或编辑。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import CHROMA_DIR, DOCS_DIR, UPLOAD_DIR, ZHIPU_API_KEY
from rag_engine import ask, build_index, collection_count

st.set_page_config(page_title="企业报销知识问答", layout="wide")

st.title("企业报销知识问答")
st.caption("基于本地 Chroma 知识库与智谱；密钥请在项目根目录 `.env` 中配置。")

if "messages" not in st.session_state:
    st.session_state.messages = []


def _safe_upload_name(name: str) -> str:
    p = Path(name).name
    if not p.lower().endswith(".md"):
        p += ".md"
    return p.replace("..", "_").replace("/", "_").replace("\\", "_")


def _list_uploaded_md() -> list[str]:
    if not UPLOAD_DIR.is_dir():
        return []
    return sorted([x.name for x in UPLOAD_DIR.glob("*.md")])


_api_key = ZHIPU_API_KEY.strip()
_has_key = bool(_api_key)

with st.sidebar:
    st.subheader("知识库")
    st.text(f"内置文档：{DOCS_DIR}")
    st.text(f"上传目录：{UPLOAD_DIR}")
    st.text(f"向量库：{CHROMA_DIR}")
    n_chunks = collection_count()
    st.metric("已索引片段数", n_chunks)

    st.subheader("文档管理（PRD）")
    st.caption("上传的 `.md` 与 `docs/` 内制度合并入库。")
    official = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    if official:
        with st.expander("内置制度（只读）", expanded=False):
            for p in official:
                st.text(f"· {p.name}")
    else:
        st.warning("`docs/` 下暂无 .md")

    uploaded = st.file_uploader(
        "上传 Markdown",
        type=["md"],
        accept_multiple_files=True,
        help="保存至 data/uploads/，重建索引后生效。",
    )
    if uploaded and st.button("保存上传的文件", type="secondary"):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in uploaded:
            dest = UPLOAD_DIR / _safe_upload_name(f.name)
            dest.write_bytes(f.getvalue())
        st.success(f"已保存 {len(uploaded)} 个文件。")
        st.rerun()

    for fname in _list_uploaded_md():
        c1, c2 = st.columns([4, 1])
        c1.caption(fname)
        if c2.button("删", key=f"rm_{fname}", help="删除上传文件"):
            try:
                (UPLOAD_DIR / fname).unlink()
                st.rerun()
            except OSError as e:
                st.error(str(e))

    st.divider()
    force = st.checkbox("强制重建（覆盖已有索引）", value=False)
    if st.button("重建索引", type="primary"):
        if not _has_key:
            st.error("未检测到 ZHIPU_API_KEY，请在项目根目录 `.env` 中配置后重启应用。")
        else:
            with st.spinner("正在向量化并写入 Chroma…"):
                result = build_index(_api_key, force=force)
            if result.get("ok"):
                st.success(result["message"])
                if not result.get("skipped"):
                    st.rerun()
            else:
                st.error(result.get("message", "索引失败"))

    st.divider()
    if st.button("清空对话"):
        st.session_state.messages = []
        st.rerun()

if not _has_key:
    st.warning("请先在项目根目录创建 `.env`，并设置 `ZHIPU_API_KEY=你的密钥`，保存后重启 Streamlit。")
elif n_chunks == 0:
    st.info("尚未建立索引。请确保 `docs/` 或上传目录中有 `.md`，然后在左侧栏点击 **重建索引**。")

# ================= 显示历史消息 =================
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("sources"):
            with st.expander("参考片段"):
                for i, s in enumerate(msg["sources"], 1):
                    dist = f"{s.distance:.4f}" if s.distance is not None else "—"
                    sec = f" · {s.section_path}" if getattr(s, "section_path", None) else ""
                    st.markdown(
                        f"**{i}. {s.source}**{sec}（块 {s.chunk_index}，距离 {dist}）"
                    )
                    st.caption(s.text[:1200] + ("…" if len(s.text) > 1200 else ""))
        # 反馈按钮（只在助手消息后显示）
        if msg["role"] == "assistant":
            col1, col2, _ = st.columns([1, 1, 8])
            feedback = msg.get("feedback")
            if feedback == "good":
                st.success("✅ 已标记为有用", icon="👍")
            elif feedback == "bad":
                st.error("❌ 已标记为无用（Bad Case）", icon="👎")
            else:
                if col1.button("👍 有用", key=f"up_{idx}"):
                    st.session_state.messages[idx]["feedback"] = "good"
                    st.rerun()
                if col2.button("👎 没用", key=f"down_{idx}"):
                    st.session_state.messages[idx]["feedback"] = "bad"
                    st.rerun()

# ================= 处理用户输入 =================
prompt = st.chat_input("请输入与报销相关的问题…")
if prompt and _has_key:
    # 1. 先添加用户消息（无 sources）
    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
        "feedback": None
    })

    if n_chunks == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "当前知识库为空，请先在左侧栏执行「重建索引」。",
            "sources": [],
            "feedback": None
        })
    else:
        try:
            with st.spinner("检索并生成中…"):
                out = ask(_api_key, prompt)   # out 是 RAGAnswer 对象
            st.session_state.messages.append({
                "role": "assistant",
                "content": out.answer,
                "sources": out.sources,
                "feedback": None
            })
        except Exception as e:
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"调用失败：{e}",
                "sources": [],
                "feedback": None
            })

    st.rerun()