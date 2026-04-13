"""
Streamlit 入口：企业报销知识问答 Web 界面（SaaS 风格优化版）
API Key 仅在项目根目录 `.env` 中配置（见 `config.py`），不在页面展示或编辑。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import CHROMA_DIR, DOCS_DIR, UPLOAD_DIR, ZHIPU_API_KEY
from rag_engine import ask, build_index, collection_count

# ================= 页面配置 =================
st.set_page_config(
    page_title="企业报销知识问答 | Expense RAG QA",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= SaaS 风格自定义 CSS =================
st.markdown("""
<style>
    /* 全局字体和颜色 */
    .stApp {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    }
    
    /* 主标题样式 */
    .main-title {
        font-size: 2rem !important;
        font-weight: 700 !important;
        color: #1a1a2e !important;
        margin-bottom: 0.5rem !important;
    }
    
    /* 副标题样式 */
    .subtitle {
        font-size: 0.9rem !important;
        color: #64748b !important;
        margin-bottom: 2rem !important;
    }
    
    /* 侧边栏标题 */
    .sidebar-title {
        font-size: 0.75rem !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        margin-top: 1.5rem !important;
        margin-bottom: 0.75rem !important;
    }
    
    /* 统计卡片 */
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 12px;
        color: white;
        text-align: center;
    }
    
    /* 文档列表项 */
    .doc-item {
        padding: 0.5rem 0.75rem;
        background: #f8fafc;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        border-left: 3px solid #3b82f6;
    }
    
    /* 聊天消息样式 */
    .stChatMessage {
        border-radius: 12px !important;
        margin-bottom: 1rem !important;
    }
    
    /* 用户消息 */
    .stChatMessage[data-testid="stChatMessageUser"] {
        background: #eff6ff !important;
        border: 1px solid #dbeafe !important;
    }
    
    /* 助手消息 */
    .stChatMessage[data-testid="stChatMessageAssistant"] {
        background: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1) !important;
    }
    
    /* 参考片段样式 */
    .source-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        margin-top: 0.5rem;
    }
    
    /* 按钮样式优化 */
    .stButton > button {
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
    }
    
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
    }
    
    /* 主要按钮 */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%) !important;
        border: none !important;
    }
    
    /* 次要按钮 */
    .stButton > button[kind="secondary"] {
        background: #f1f5f9 !important;
        color: #475569 !important;
        border: 1px solid #cbd5e1 !important;
    }
    
    /* 警告提示 */
    .stAlert {
        border-radius: 10px !important;
        border: none !important;
    }
    
    /* 文件上传区域 */
    .stFileUploader {
        border: 2px dashed #cbd5e1 !important;
        border-radius: 12px !important;
        padding: 1rem !important;
    }
    
    /* 分割线 */
    hr {
        border-color: #e2e8f0 !important;
        margin: 1.5rem 0 !important;
    }
    
    /* 反馈按钮 */
    .feedback-btn {
        font-size: 0.85rem !important;
        padding: 0.25rem 0.75rem !important;
    }
</style>
""", unsafe_allow_html=True)

# ================= 初始化状态 =================
if "messages" not in st.session_state:
    st.session_state.messages = []

# ================= 工具函数 =================
def _safe_upload_name(name: str) -> str:
    p = Path(name).name
    if not p.lower().endswith(".md"):
        p += ".md"
    return p.replace("..", "_").replace("/", "_").replace("\\", "_")


def _list_uploaded_md() -> list[str]:
    if not UPLOAD_DIR.is_dir():
        return []
    return sorted([x.name for x in UPLOAD_DIR.glob("*.md")])


# ================= 主界面 =================
# 顶部标题区域
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown('<p class="main-title">💼 企业报销知识问答</p>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">基于 RAG 技术的智能报销政策助手 · 7×24小时自助服务</p>', unsafe_allow_html=True)

with col2:
    n_chunks = collection_count()
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                padding: 1rem; border-radius: 12px; color: white; text-align: center;">
        <div style="font-size: 1.5rem; font-weight: 700;">{n_chunks}</div>
        <div style="font-size: 0.75rem; opacity: 0.9;">已索引文档片段</div>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# API Key 检查
_api_key = ZHIPU_API_KEY.strip()
_has_key = bool(_api_key)

# ================= 侧边栏 =================
with st.sidebar:
    # 品牌区域
    st.markdown("""
    <div style="padding: 1rem 0; border-bottom: 1px solid #e2e8f0; margin-bottom: 1rem;">
        <div style="font-size: 1.25rem; font-weight: 700; color: #1e293b;">Expense RAG QA</div>
        <div style="font-size: 0.75rem; color: #64748b;">v5.0 · 企业级报销助手</div>
    </div>
    """, unsafe_allow_html=True)
    
    # 系统状态
    st.markdown('<p class="sidebar-title">系统状态</p>', unsafe_allow_html=True)
    
    status_col1, status_col2 = st.columns(2)
    with status_col1:
        if _has_key:
            st.success("✓ API已连接")
        else:
            st.error("✗ API未配置")
    with status_col2:
        if n_chunks > 0:
            st.success(f"✓ 知识库就绪")
        else:
            st.warning("⚠ 待建索引")
    
    st.markdown(f"""
    <div style="background: #f8fafc; padding: 0.75rem; border-radius: 8px; margin-top: 0.5rem;">
        <div style="font-size: 0.75rem; color: #64748b; margin-bottom: 0.25rem;">存储路径</div>
        <div style="font-size: 0.8rem; color: #334155; font-family: monospace;">
            📁 docs/ · {len(list(DOCS_DIR.glob('*.md')))} 个文件<br>
            📤 uploads/ · {len(_list_uploaded_md())} 个文件<br>
            💾 chroma/ · {n_chunks} 个向量
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # 文档管理
    st.markdown('<p class="sidebar-title">文档管理</p>', unsafe_allow_html=True)
    
    # 内置文档列表
    official = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    if official:
        with st.expander(f"📚 内置制度 ({len(official)}个)", expanded=False):
            for p in official:
                st.markdown(f'<div class="doc-item">📄 {p.name}</div>', unsafe_allow_html=True)
    
    # 文件上传
    uploaded = st.file_uploader(
        "⬆️ 上传 Markdown 文档",
        type=["md"],
        accept_multiple_files=True,
        help="支持多个文件上传，保存至 data/uploads/",
    )
    if uploaded and st.button("💾 保存上传的文件", type="secondary", use_container_width=True):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in uploaded:
            dest = UPLOAD_DIR / _safe_upload_name(f.name)
            dest.write_bytes(f.getvalue())
        st.success(f"✅ 已保存 {len(uploaded)} 个文件")
        st.rerun()
    
    # 已上传文件列表
    uploaded_files = _list_uploaded_md()
    if uploaded_files:
        st.caption(f"已上传文件 ({len(uploaded_files)}个)")
        for fname in uploaded_files:
            col1, col2 = st.columns([4, 1])
            col1.markdown(f'<div style="font-size: 0.8rem; color: #475569;">📄 {fname}</div>', unsafe_allow_html=True)
            if col2.button("🗑️", key=f"rm_{fname}", help="删除"):
                try:
                    (UPLOAD_DIR / fname).unlink()
                    st.rerun()
                except OSError as e:
                    st.error(str(e))
    
    st.divider()
    
    # 索引管理
    st.markdown('<p class="sidebar-title">索引管理</p>', unsafe_allow_html=True)
    
    force = st.checkbox("🔄 强制重建（覆盖已有）", value=False)
    if st.button("🔨 重建知识库索引", type="primary", use_container_width=True):
        if not _has_key:
            st.error("❌ 未检测到 ZHIPU_API_KEY，请在 .env 中配置")
        else:
            with st.spinner("正在向量化文档并写入 Chroma…"):
                result = build_index(_api_key, force=force)
            if result.get("ok"):
                st.success(f"✅ {result['message']}")
                if not result.get("skipped"):
                    st.rerun()
            else:
                st.error(f"❌ {result.get('message', '索引失败')}")
    
    st.divider()
    
    # 对话管理
    st.markdown('<p class="sidebar-title">对话管理</p>', unsafe_allow_html=True)
    if st.button("🗑️ 清空当前对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    
    # 底部信息
    st.divider()
    st.markdown("""
    <div style="font-size: 0.7rem; color: #94a3b8; text-align: center;">
        基于智谱 GLM-4.5-Air · 本地 BGE Embedding<br>
        <a href="https://github.com/pleaselikeme/expense-rag-qa" target="_blank" style="color: #64748b;">GitHub</a>
    </div>
    """, unsafe_allow_html=True)

# ================= 主内容区 =================
# 状态提示
if not _has_key:
    st.warning("⚠️ **API Key 未配置**：请在项目根目录创建 `.env`，设置 `ZHIPU_API_KEY=你的密钥`，保存后重启应用。")
elif n_chunks == 0:
    st.info("📚 **知识库为空**：请确保 `docs/` 或上传目录中有 `.md` 文件，然后点击左侧「重建知识库索引」。")

# ================= 对话区域 =================
st.markdown("""
<div style="background: #f8fafc; padding: 1rem; border-radius: 12px; margin-bottom: 1rem;">
    <div style="font-size: 0.85rem; color: #64748b;">
        💡 <b>使用提示</b>：请提出与报销相关的问题，例如"差旅费报销标准是多少？"、"发票丢了怎么办？"<br>
        🛡️ 系统会自动拒绝薪资、考勤等非报销类问题
    </div>
</div>
""", unsafe_allow_html=True)

# 显示历史消息
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        
        # 助手消息显示参考来源
        if msg["role"] == "assistant" and msg.get("sources"):
            with st.expander("📚 参考来源", expanded=False):
                for i, s in enumerate(msg["sources"], 1):
                    dist = f"{s.distance:.4f}" if s.distance is not None else "—"
                    sec = f" · {s.section_path}" if getattr(s, "section_path", None) else ""
                    
                    st.markdown(f"""
                    <div style="background: #f1f5f9; padding: 0.75rem; border-radius: 8px; margin-bottom: 0.5rem; border-left: 3px solid #3b82f6;">
                        <div style="font-size: 0.8rem; font-weight: 600; color: #1e293b; margin-bottom: 0.25rem;">
                            {i}. {s.source}{sec}
                        </div>
                        <div style="font-size: 0.7rem; color: #64748b; margin-bottom: 0.5rem;">
                            块 {s.chunk_index} · 相似度 {dist}
                        </div>
                        <div style="font-size: 0.8rem; color: #475569; line-height: 1.5;">
                            {s.text[:500]}{"…" if len(s.text) > 500 else ""}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
        
        # 反馈按钮
        if msg["role"] == "assistant":
            feedback = msg.get("feedback")
            col1, col2, col3 = st.columns([1, 1, 10])
            
            if feedback == "good":
                col1.success("👍 有用")
            elif feedback == "bad":
                col2.error("👎 无用")
            else:
                if col1.button("👍 有用", key=f"up_{idx}", help="标记为有用"):
                    st.session_state.messages[idx]["feedback"] = "good"
                    st.rerun()
                if col2.button("👎 无用", key=f"down_{idx}", help="标记为无用（Bad Case）"):
                    st.session_state.messages[idx]["feedback"] = "bad"
                    st.rerun()

# ================= 处理用户输入 =================
prompt = st.chat_input("💬 请输入与报销相关的问题…")
if prompt and _has_key:
    # 添加用户消息
    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
        "feedback": None
    })

    if n_chunks == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "⚠️ 当前知识库为空，请先在左侧栏执行「重建知识库索引」。",
            "sources": [],
            "feedback": None
        })
    else:
        try:
            with st.spinner("🔍 检索知识库并生成回答…"):
                out = ask(_api_key, prompt)
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": out.answer,
                "sources": out.sources,
                "feedback": None
            })
        except Exception as e:
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ 调用失败：{e}",
                "sources": [],
                "feedback": None
            })

    st.rerun()
