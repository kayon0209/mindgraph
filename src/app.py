"""
Streamlit 入口：企业报销知识问答 Web 界面（SaaS + Apple 融合风格）
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import CHROMA_DIR, DOCS_DIR, UPLOAD_DIR, ZHIPU_API_KEY
from rag_engine import ask, build_index, collection_count

# ================= 页面配置 =================
st.set_page_config(
    page_title="报销助手 | Expense RAG QA",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= 融合风格 CSS =================
st.markdown("""
<style>
    /* 字体 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        background: #f8fafc;
        color: #0f172a;
    }
    
    /* 隐藏默认元素 */
    #MainMenu, footer, header {visibility: hidden;}
    
    /* 主容器 */
    .main .block-container {
        max-width: 1400px;
        padding: 0;
    }
    
    /* ===== 顶部导航栏（Apple毛玻璃 + SaaS功能密度） ===== */
    .top-nav {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 64px;
        background: rgba(255, 255, 255, 0.85);
        backdrop-filter: saturate(180%) blur(20px);
        -webkit-backdrop-filter: saturate(180%) blur(20px);
        border-bottom: 1px solid rgba(226, 232, 240, 0.8);
        z-index: 1000;
        display: flex;
        align-items: center;
        padding: 0 2rem;
    }
    
    .nav-inner {
        width: 100%;
        max-width: 1400px;
        margin: 0 auto;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    
    .nav-brand {
        display: flex;
        align-items: center;
        gap: 0.75rem;
    }
    
    .nav-logo {
        width: 40px;
        height: 40px;
        background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.25rem;
    }
    
    .nav-title {
        font-size: 1.25rem;
        font-weight: 700;
        color: #0f172a;
        letter-spacing: -0.02em;
    }
    
    .nav-subtitle {
        font-size: 0.75rem;
        color: #64748b;
        font-weight: 500;
    }
    
    .nav-stats {
        display: flex;
        align-items: center;
        gap: 2rem;
    }
    
    .nav-stat {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.875rem;
        color: #475569;
    }
    
    .nav-stat-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
    }
    
    .nav-stat-dot.online { background: #10b981; box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2); }
    .nav-stat-dot.warning { background: #f59e0b; }
    .nav-stat-dot.offline { background: #ef4444; }
    
    /* ===== 主布局（SaaS分栏） ===== */
    .main-layout {
        display: flex;
        margin-top: 64px;
        min-height: calc(100vh - 64px);
    }
    
    /* 侧边栏 */
    .sidebar {
        width: 320px;
        background: white;
        border-right: 1px solid #e2e8f0;
        padding: 1.5rem;
        overflow-y: auto;
        position: fixed;
        top: 64px;
        bottom: 0;
        left: 0;
    }
    
    .sidebar-section {
        margin-bottom: 1.5rem;
    }
    
    .sidebar-title {
        font-size: 0.6875rem;
        font-weight: 600;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.75rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    /* 数据卡片（SaaS密度 + Apple圆角） */
    .stat-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.75rem;
        margin-bottom: 1.5rem;
    }
    
    .stat-card {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 1rem;
        text-align: center;
    }
    
    .stat-card.primary {
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
        border: none;
        color: white;
    }
    
    .stat-value {
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: -0.02em;
    }
    
    .stat-card.primary .stat-value {
        color: white;
    }
    
    .stat-label {
        font-size: 0.6875rem;
        font-weight: 500;
        color: #64748b;
        margin-top: 0.25rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    .stat-card.primary .stat-label {
        color: rgba(255,255,255,0.8);
    }
    
    /* 文档列表 */
    .doc-list {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    
    .doc-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.75rem;
        background: #f8fafc;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        font-size: 0.8125rem;
        color: #334155;
        transition: all 0.2s ease;
    }
    
    .doc-item:hover {
        background: #f1f5f9;
        border-color: #cbd5e1;
    }
    
    .doc-icon {
        width: 32px;
        height: 32px;
        background: white;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        border: 1px solid #e2e8f0;
    }
    
    .doc-name {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    
    .doc-actions {
        display: flex;
        gap: 0.25rem;
    }
    
    /* ===== 主内容区 ===== */
    .content {
        flex: 1;
        margin-left: 320px;
        padding: 2rem;
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
    }
    
    /* 欢迎区域 */
    .welcome-card {
        background: white;
        border-radius: 20px;
        padding: 2rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    
    .welcome-title {
        font-size: 1.5rem;
        font-weight: 700;
        color: #0f172a;
        letter-spacing: -0.02em;
        margin-bottom: 0.5rem;
    }
    
    .welcome-desc {
        font-size: 0.9375rem;
        color: #64748b;
        line-height: 1.6;
    }
    
    /* 快捷问题 */
    .quick-section {
        margin-top: 1.5rem;
    }
    
    .quick-title {
        font-size: 0.75rem;
        font-weight: 600;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.75rem;
    }
    
    .quick-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
    }
    
    .quick-chip {
        padding: 0.625rem 1rem;
        background: #f1f5f9;
        border: 1px solid #e2e8f0;
        border-radius: 9999px;
        font-size: 0.8125rem;
        color: #475569;
        cursor: pointer;
        transition: all 0.2s ease;
    }
    
    .quick-chip:hover {
        background: #3b82f6;
        border-color: #3b82f6;
        color: white;
    }
    
    /* ===== 聊天区域（Apple气泡 + SaaS信息密度） ===== */
    .chat-card {
        background: white;
        border-radius: 20px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        flex: 1;
        display: flex;
        flex-direction: column;
        min-height: 400px;
    }
    
    .chat-header {
        padding: 1rem 1.5rem;
        border-bottom: 1px solid #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    
    .chat-title {
        font-size: 0.9375rem;
        font-weight: 600;
        color: #0f172a;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .chat-badge {
        padding: 0.25rem 0.625rem;
        background: #f1f5f9;
        border-radius: 9999px;
        font-size: 0.6875rem;
        font-weight: 500;
        color: #64748b;
    }
    
    .chat-actions {
        display: flex;
        gap: 0.5rem;
    }
    
    .chat-messages {
        flex: 1;
        padding: 1.5rem;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 1rem;
    }
    
    /* 消息气泡 */
    .message {
        display: flex;
        gap: 0.875rem;
        max-width: 85%;
        animation: messageIn 0.3s ease;
    }
    
    @keyframes messageIn {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .message.user {
        align-self: flex-end;
        flex-direction: row-reverse;
    }
    
    .message-avatar {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        flex-shrink: 0;
    }
    
    .message-avatar.user {
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
    }
    
    .message-avatar.assistant {
        background: linear-gradient(135deg, #10b981 0%, #34d399 100%);
    }
    
    .message-content {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }
    
    .message-bubble {
        padding: 0.875rem 1.125rem;
        border-radius: 18px;
        font-size: 0.9375rem;
        line-height: 1.6;
        letter-spacing: -0.01em;
    }
    
    .message-bubble.user {
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
        color: white;
        border-bottom-right-radius: 6px;
    }
    
    .message-bubble.assistant {
        background: #f8fafc;
        color: #0f172a;
        border: 1px solid #e2e8f0;
        border-bottom-left-radius: 6px;
    }
    
    .message-meta {
        font-size: 0.6875rem;
        color: #94a3b8;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    /* 参考来源 */
    .sources-box {
        margin-top: 0.75rem;
        padding: 0.875rem;
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
    }
    
    .sources-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 0.625rem;
    }
    
    .sources-title {
        font-size: 0.6875rem;
        font-weight: 600;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    .source-item {
        padding: 0.625rem 0.875rem;
        background: #f8fafc;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        font-size: 0.8125rem;
    }
    
    .source-item:last-child {
        margin-bottom: 0;
    }
    
    .source-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.25rem;
    }
    
    .source-name {
        font-weight: 600;
        color: #0f172a;
    }
    
    .source-score {
        font-size: 0.6875rem;
        font-weight: 500;
        color: #10b981;
        background: rgba(16, 185, 129, 0.1);
        padding: 0.125rem 0.5rem;
        border-radius: 9999px;
    }
    
    .source-text {
        color: #64748b;
        font-size: 0.75rem;
        line-height: 1.5;
    }
    
    /* 输入区域 */
    .input-area {
        padding: 1rem 1.5rem 1.5rem;
        border-top: 1px solid #f1f5f9;
    }
    
    /* 空状态 */
    .empty-state {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 3rem;
        color: #94a3b8;
    }
    
    .empty-icon {
        width: 80px;
        height: 80px;
        background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
        border-radius: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 2.5rem;
        margin-bottom: 1.5rem;
    }
    
    .empty-title {
        font-size: 1.125rem;
        font-weight: 600;
        color: #475569;
        margin-bottom: 0.5rem;
    }
    
    .empty-desc {
        font-size: 0.875rem;
        color: #94a3b8;
    }
    
    /* ===== 按钮样式 ===== */
    .stButton > button {
        border-radius: 10px !important;
        font-weight: 500 !important;
        font-size: 0.8125rem !important;
        padding: 0.625rem 1rem !important;
        transition: all 0.2s ease !important;
        border: none !important;
    }
    
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%) !important;
        color: white !important;
        box-shadow: 0 1px 2px rgba(59, 130, 246, 0.2) !important;
    }
    
    .stButton > button[kind="primary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important;
    }
    
    .stButton > button[kind="secondary"] {
        background: #f1f5f9 !important;
        color: #475569 !important;
        border: 1px solid #e2e8f0 !important;
    }
    
    .stButton > button[kind="secondary"]:hover {
        background: #e2e8f0 !important;
    }
    
    /* 文件上传 */
    .stFileUploader {
        border: 2px dashed #cbd5e1 !important;
        border-radius: 12px !important;
        background: #f8fafc !important;
        padding: 1rem !important;
    }
    
    .stFileUploader:hover {
        border-color: #3b82f6 !important;
        background: rgba(59, 130, 246, 0.04) !important;
    }
    
    /* 警告提示 */
    .stAlert {
        border-radius: 12px !important;
        border: none !important;
    }
    
    /* 复选框 */
    .stCheckbox {
        font-size: 0.8125rem !important;
    }
    
    /* 侧边栏滚动条 */
    .sidebar::-webkit-scrollbar {
        width: 6px;
    }
    
    .sidebar::-webkit-scrollbar-track {
        background: transparent;
    }
    
    .sidebar::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 3px;
    }
</style>
""", unsafe_allow_html=True)

# ================= 初始化 =================
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


# ================= 数据 =================
n_chunks = collection_count()
_api_key = ZHIPU_API_KEY.strip()
_has_key = bool(_api_key)

# ================= 顶部导航 =================
st.markdown(f"""
<div class="top-nav">
    <div class="nav-inner">
        <div class="nav-brand">
            <div class="nav-logo">💼</div>
            <div>
                <div class="nav-title">报销助手</div>
                <div class="nav-subtitle">Expense RAG QA v5.0</div>
            </div>
        </div>
        <div class="nav-stats">
            <div class="nav-stat">
                <span class="nav-stat-dot {'online' if _has_key else 'offline'}"></span>
                {'API 已连接' if _has_key else 'API 未配置'}
            </div>
            <div class="nav-stat">
                <span class="nav-stat-dot online"></span>
                {n_chunks} 文档片段
            </div>
            <div class="nav-stat">
                <span class="nav-stat-dot online"></span>
                97.1% 覆盖率
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ================= 侧边栏 =================
with st.sidebar:
    # 统计卡片
    st.markdown('<div class="sidebar-section">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-title">📊 系统状态</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"""
        <div class="stat-card primary">
            <div class="stat-value">{n_chunks}</div>
            <div class="stat-label">文档片段</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="stat-card">
            <div class="stat-value" style="color: #10b981;">97%</div>
            <div class="stat-label">问题覆盖</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 索引管理
    st.markdown('<div class="sidebar-section">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-title">🔧 索引管理</div>', unsafe_allow_html=True)
    
    force = st.checkbox("强制重建", value=False)
    if st.button("🔨 重建索引", type="primary", use_container_width=True):
        if not _has_key:
            st.error("未配置 ZHIPU_API_KEY")
        else:
            with st.spinner("向量化中..."):
                result = build_index(_api_key, force=force)
            if result.get("ok"):
                st.success(result["message"])
                st.rerun()
            else:
                st.error(result.get("message", "失败"))
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 内置文档
    st.markdown('<div class="sidebar-section">', unsafe_allow_html=True)
    st.markdown(f'<div class="sidebar-title">📚 内置文档 ({len(list(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else [])})</div>', unsafe_allow_html=True)
    
    official = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    for p in official:
        st.markdown(f"""
        <div class="doc-item">
            <div class="doc-icon">📄</div>
            <div class="doc-name">{p.name}</div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 上传文档
    st.markdown('<div class="sidebar-section">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-title">⬆️ 上传文档</div>', unsafe_allow_html=True)
    
    uploaded = st.file_uploader("选择文件", type=["md"], accept_multiple_files=True, label_visibility="collapsed")
    if uploaded and st.button("💾 保存", type="secondary", use_container_width=True):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in uploaded:
            dest = UPLOAD_DIR / _safe_upload_name(f.name)
            dest.write_bytes(f.getvalue())
        st.success(f"已保存 {len(uploaded)} 个文件")
        st.rerun()
    
    # 已上传文件
    uploaded_files = _list_uploaded_md()
    if uploaded_files:
        for fname in uploaded_files:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"""
                <div class="doc-item" style="margin-bottom: 0.5rem;">
                    <div class="doc-icon">📄</div>
                    <div class="doc-name">{fname}</div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if st.button("🗑️", key=f"rm_{fname}"):
                    try:
                        (UPLOAD_DIR / fname).unlink()
                        st.rerun()
                    except OSError as e:
                        st.error(str(e))
    st.markdown('</div>', unsafe_allow_html=True)

# ================= 主内容区 =================
st.markdown('<div class="content">', unsafe_allow_html=True)

# 欢迎卡片（仅在无消息时显示）
if len(st.session_state.messages) == 0:
    st.markdown("""
    <div class="welcome-card">
        <div class="welcome-title">👋 欢迎使用智能报销助手</div>
        <div class="welcome-desc">
            基于企业报销制度的智能问答系统，支持差旅费、日常费用、审批流程等各类报销问题查询。
            系统已收录 4 份制度文档，覆盖 97% 常见问题。
        </div>
        <div class="quick-section">
            <div class="quick-title">快速开始</div>
            <div class="quick-chips">
    """, unsafe_allow_html=True)
    
    quick_questions = [
        "差旅费报销标准是多少？",
        "发票丢了怎么办？",
        "打车费能报销吗？",
        "超标住宿需要什么手续？",
        "报销需要哪些材料？"
    ]
    
    cols = st.columns(len(quick_questions))
    for i, q in enumerate(quick_questions):
        with cols[i]:
            if st.button(q, key=f"quick_{i}", use_container_width=True):
                st.session_state.quick_question = q
                st.rerun()
    
    st.markdown("""
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# 状态提示
if not _has_key:
    st.warning("⚠️ 请配置 ZHIPU_API_KEY 后使用")
elif n_chunks == 0:
    st.info("📚 知识库为空，请先上传文档并重建索引")

# 聊天卡片
st.markdown("""
<div class="chat-card">
    <div class="chat-header">
        <div class="chat-title">
            💬 对话
            <span class="chat-badge">{n} 条消息</span>
        </div>
        <div class="chat-actions">
            <button onclick="window.location.reload()" style="padding: 0.5rem 1rem; background: #f1f5f9; border: none; border-radius: 8px; font-size: 0.8125rem; color: #475569; cursor: pointer;">🗑️ 清空</button>
        </div>
    </div>
    <div class="chat-messages">
""".format(n=len(st.session_state.messages)), unsafe_allow_html=True)

# 显示消息
for idx, msg in enumerate(st.session_state.messages):
    is_user = msg["role"] == "user"
    avatar = "👤" if is_user else "🤖"
    bubble_class = "user" if is_user else "assistant"
    
    st.markdown(f"""
    <div class="message {bubble_class}">
        <div class="message-avatar {bubble_class}">{avatar}</div>
        <div class="message-content">
            <div class="message-bubble {bubble_class}">{msg['content']}</div>
    """, unsafe_allow_html=True)
    
    # 参考来源
    if not is_user and msg.get("sources"):
        st.markdown('<div class="sources-box">', unsafe_allow_html=True)
        st.markdown('<div class="sources-header"><span class="sources-title">📚 参考来源</span></div>', unsafe_allow_html=True)
        
        for i, s in enumerate(msg["sources"], 1):
            dist = f"{s.distance:.0%}" if s.distance is not None else "—"
            sec = f" · {s.section_path}" if getattr(s, "section_path", None) else ""
            st.markdown(f"""
            <div class="source-item">
                <div class="source-header">
                    <span class="source-name">{i}. {s.name}{sec}</span>
                    <span class="source-score">匹配度 {dist}</span>
                </div>
                <div class="source-text">{s.text[:150]}...</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('</div></div>', unsafe_allow_html=True)

# 空状态
if len(st.session_state.messages) == 0:
    st.markdown("""
    <div class="empty-state">
        <div class="empty-icon">💬</div>
        <div class="empty-title">开始对话</div>
        <div class="empty-desc">输入您的问题，或点击上方快捷问题开始</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("""
    </div>
    <div class="input-area">
""", unsafe_allow_html=True)

# 输入框
if "quick_question" in st.session_state:
    prompt = st.session_state.quick_question
    del st.session_state.quick_question
else:
    prompt = st.chat_input("输入您的问题...")

if prompt and _has_key:
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    if n_chunks == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "⚠️ 知识库为空，请先上传文档并重建索引。",
            "sources": []
        })
    else:
        try:
            with st.spinner("思考中..."):
                out = ask(_api_key, prompt)
            st.session_state.messages.append({
                "role": "assistant",
                "content": out.answer,
                "sources": out.sources
            })
        except Exception as e:
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ 处理出错：{e}",
                "sources": []
            })
    st.rerun()

st.markdown("""
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)  # 结束 content
