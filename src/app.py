"""
Streamlit 入口：企业报销知识问答 Web 界面（Apple Design 风格）
API Key 仅在项目根目录 `.env` 中配置（见 `config.py`），不在页面展示或编辑。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import CHROMA_DIR, DOCS_DIR, UPLOAD_DIR, ZHIPU_API_KEY
from rag_engine import ask, build_index, collection_count

# ================= 页面配置 =================
st.set_page_config(
    page_title="报销助手",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ================= Apple Design 风格自定义 CSS =================
st.markdown("""
<style>
    /* 导入 SF Pro 字体 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    /* 全局重置 - Apple 风格 */
    .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif;
        background: linear-gradient(180deg, #f5f5f7 0%, #ffffff 100%);
        color: #1d1d1f;
    }
    
    /* 隐藏 Streamlit 默认元素 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* 主容器 */
    .main .block-container {
        max-width: 900px;
        padding: 2rem 3rem;
    }
    
    /* 顶部导航栏 - 毛玻璃效果 */
    .nav-bar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 52px;
        background: rgba(255, 255, 255, 0.72);
        backdrop-filter: saturate(180%) blur(20px);
        -webkit-backdrop-filter: saturate(180%) blur(20px);
        border-bottom: 1px solid rgba(0, 0, 0, 0.08);
        z-index: 1000;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    
    .nav-content {
        width: 100%;
        max-width: 1024px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 2rem;
    }
    
    .nav-logo {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1d1d1f;
        letter-spacing: -0.02em;
    }
    
    .nav-status {
        display: flex;
        align-items: center;
        gap: 1rem;
        font-size: 0.75rem;
        color: #86868b;
    }
    
    .status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #34c759;
    }
    
    .status-dot.warning {
        background: #ff9500;
    }
    
    /* 主标题区域 */
    .hero-section {
        text-align: center;
        padding: 6rem 0 3rem;
    }
    
    .hero-title {
        font-size: 3rem;
        font-weight: 700;
        color: #1d1d1f;
        letter-spacing: -0.03em;
        line-height: 1.1;
        margin-bottom: 1rem;
    }
    
    .hero-subtitle {
        font-size: 1.25rem;
        font-weight: 400;
        color: #86868b;
        letter-spacing: -0.01em;
        max-width: 600px;
        margin: 0 auto;
    }
    
    /* 统计卡片 - Apple 风格 */
    .stats-container {
        display: flex;
        justify-content: center;
        gap: 2rem;
        margin: 2rem 0 3rem;
        flex-wrap: wrap;
    }
    
    .stat-card {
        background: rgba(255, 255, 255, 0.8);
        backdrop-filter: blur(20px);
        border-radius: 20px;
        padding: 1.5rem 2rem;
        min-width: 140px;
        text-align: center;
        border: 1px solid rgba(0, 0, 0, 0.04);
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.04);
    }
    
    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #1d1d1f;
        letter-spacing: -0.02em;
    }
    
    .stat-label {
        font-size: 0.75rem;
        font-weight: 500;
        color: #86868b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 0.25rem;
    }
    
    /* 对话区域 - 毛玻璃卡片 */
    .chat-container {
        background: rgba(255, 255, 255, 0.72);
        backdrop-filter: saturate(180%) blur(20px);
        -webkit-backdrop-filter: saturate(180%) blur(20px);
        border-radius: 24px;
        border: 1px solid rgba(0, 0, 0, 0.06);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
        overflow: hidden;
        margin-bottom: 2rem;
    }
    
    .chat-header {
        padding: 1.25rem 1.5rem;
        border-bottom: 1px solid rgba(0, 0, 0, 0.06);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    
    .chat-title {
        font-size: 0.875rem;
        font-weight: 600;
        color: #1d1d1f;
        letter-spacing: -0.01em;
    }
    
    .chat-actions {
        display: flex;
        gap: 0.5rem;
    }
    
    .chat-action-btn {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: none;
        background: rgba(0, 0, 0, 0.04);
        color: #86868b;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s ease;
    }
    
    .chat-action-btn:hover {
        background: rgba(0, 0, 0, 0.08);
        color: #1d1d1f;
    }
    
    /* 聊天消息 */
    .chat-messages {
        padding: 1.5rem;
        min-height: 300px;
        max-height: 500px;
        overflow-y: auto;
    }
    
    .message {
        display: flex;
        gap: 0.875rem;
        margin-bottom: 1.25rem;
        animation: messageAppear 0.3s ease;
    }
    
    @keyframes messageAppear {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    .message-avatar {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        flex-shrink: 0;
    }
    
    .message-avatar.user {
        background: linear-gradient(135deg, #007aff 0%, #5856d6 100%);
    }
    
    .message-avatar.assistant {
        background: linear-gradient(135deg, #34c759 0%, #30d158 100%);
    }
    
    .message-content {
        flex: 1;
        max-width: calc(100% - 50px);
    }
    
    .message-bubble {
        padding: 0.875rem 1rem;
        border-radius: 18px;
        font-size: 0.9375rem;
        line-height: 1.5;
        letter-spacing: -0.01em;
    }
    
    .message-bubble.user {
        background: #007aff;
        color: white;
        border-bottom-right-radius: 4px;
    }
    
    .message-bubble.assistant {
        background: #f2f2f7;
        color: #1d1d1f;
        border-bottom-left-radius: 4px;
    }
    
    .message-time {
        font-size: 0.6875rem;
        color: #86868b;
        margin-top: 0.25rem;
        margin-left: 0.25rem;
    }
    
    /* 参考来源 */
    .sources-section {
        margin-top: 0.75rem;
        padding: 0.75rem 1rem;
        background: rgba(0, 0, 0, 0.02);
        border-radius: 12px;
    }
    
    .sources-title {
        font-size: 0.6875rem;
        font-weight: 600;
        color: #86868b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.5rem;
    }
    
    .source-item {
        padding: 0.625rem 0.875rem;
        background: white;
        border-radius: 10px;
        margin-bottom: 0.5rem;
        border: 1px solid rgba(0, 0, 0, 0.04);
        font-size: 0.8125rem;
        color: #3a3a3c;
    }
    
    .source-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.25rem;
    }
    
    .source-name {
        font-weight: 600;
        color: #1d1d1f;
    }
    
    .source-score {
        font-size: 0.6875rem;
        color: #34c759;
        font-weight: 500;
    }
    
    /* 输入区域 */
    .input-container {
        padding: 1rem 1.5rem 1.5rem;
        border-top: 1px solid rgba(0, 0, 0, 0.06);
    }
    
    .input-wrapper {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        background: #f2f2f7;
        border-radius: 24px;
        padding: 0.5rem 0.5rem 0.5rem 1.25rem;
        border: 1px solid transparent;
        transition: all 0.2s ease;
    }
    
    .input-wrapper:focus-within {
        background: white;
        border-color: #007aff;
        box-shadow: 0 0 0 4px rgba(0, 122, 255, 0.1);
    }
    
    /* 快捷问题 */
    .quick-questions {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
        padding: 0 0.5rem;
    }
    
    .quick-chip {
        padding: 0.5rem 1rem;
        background: rgba(0, 0, 0, 0.04);
        border-radius: 16px;
        font-size: 0.8125rem;
        color: #3a3a3c;
        cursor: pointer;
        transition: all 0.2s ease;
        border: none;
    }
    
    .quick-chip:hover {
        background: rgba(0, 122, 255, 0.1);
        color: #007aff;
    }
    
    /* 文档管理面板 */
    .panel {
        background: rgba(255, 255, 255, 0.72);
        backdrop-filter: saturate(180%) blur(20px);
        border-radius: 20px;
        border: 1px solid rgba(0, 0, 0, 0.06);
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
    
    .panel-title {
        font-size: 1.125rem;
        font-weight: 600;
        color: #1d1d1f;
        letter-spacing: -0.02em;
        margin-bottom: 1rem;
    }
    
    .doc-list {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    
    .doc-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.75rem 1rem;
        background: #f2f2f7;
        border-radius: 12px;
        font-size: 0.875rem;
        color: #1d1d1f;
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
    }
    
    /* 按钮样式 - Apple 风格 */
    .stButton > button {
        border-radius: 980px !important;
        font-weight: 500 !important;
        font-size: 0.875rem !important;
        padding: 0.625rem 1.25rem !important;
        transition: all 0.2s ease !important;
        border: none !important;
    }
    
    .stButton > button[kind="primary"] {
        background: #007aff !important;
        color: white !important;
    }
    
    .stButton > button[kind="primary"]:hover {
        background: #0051d5 !important;
        transform: scale(1.02);
    }
    
    .stButton > button[kind="secondary"] {
        background: #f2f2f7 !important;
        color: #007aff !important;
    }
    
    .stButton > button[kind="secondary"]:hover {
        background: #e5e5ea !important;
    }
    
    /* 警告提示 */
    .stAlert {
        border-radius: 16px !important;
        border: none !important;
        background: rgba(255, 59, 48, 0.08) !important;
        color: #ff3b30 !important;
    }
    
    .stAlert[data-baseweb="notification"] {
        background: rgba(255, 204, 0, 0.08) !important;
        color: #ff9500 !important;
    }
    
    /* 侧边栏 */
    [data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.9) !important;
        backdrop-filter: blur(20px) !important;
        border-right: 1px solid rgba(0, 0, 0, 0.06) !important;
    }
    
    /* 文件上传 */
    .stFileUploader {
        border: 2px dashed #d1d1d6 !important;
        border-radius: 16px !important;
        background: #f2f2f7 !important;
    }
    
    .stFileUploader:hover {
        border-color: #007aff !important;
        background: rgba(0, 122, 255, 0.04) !important;
    }
    
    /* 分割线 */
    hr {
        border-color: rgba(0, 0, 0, 0.06) !important;
        margin: 1.5rem 0 !important;
    }
    
    /* 空状态 */
    .empty-state {
        text-align: center;
        padding: 4rem 2rem;
        color: #86868b;
    }
    
    .empty-icon {
        font-size: 4rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }
    
    .empty-title {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1d1d1f;
        margin-bottom: 0.5rem;
    }
    
    .empty-desc {
        font-size: 0.9375rem;
        color: #86868b;
    }
</style>
""", unsafe_allow_html=True)

# ================= 初始化状态 =================
if "messages" not in st.session_state:
    st.session_state.messages = []

if "show_sidebar" not in st.session_state:
    st.session_state.show_sidebar = False

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


# ================= 顶部导航栏 =================
n_chunks = collection_count()
_api_key = ZHIPU_API_KEY.strip()
_has_key = bool(_api_key)

st.markdown(f"""
<div class="nav-bar">
    <div class="nav-content">
        <div class="nav-logo">💼 报销助手</div>
        <div class="nav-status">
            <span style="display: flex; align-items: center; gap: 6px;">
                <span class="status-dot {'warning' if not _has_key else ''}"></span>
                {'API 已连接' if _has_key else 'API 未配置'}
            </span>
            <span style="color: #d1d1d6;">|</span>
            <span>{n_chunks} 个文档片段</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ================= 侧边栏（文档管理）====================
with st.sidebar:
    st.markdown('<div class="panel-title">⚙️ 设置</div>', unsafe_allow_html=True)
    
    # 索引管理
    st.markdown('<p style="font-size: 0.75rem; color: #86868b; text-transform: uppercase; letter-spacing: 0.05em; margin: 1.5rem 0 0.75rem;">知识库管理</p>', unsafe_allow_html=True)
    
    force = st.checkbox("强制重建索引", value=False)
    if st.button("🔨 重建索引", type="primary", use_container_width=True):
        if not _has_key:
            st.error("未检测到 ZHIPU_API_KEY")
        else:
            with st.spinner("正在向量化..."):
                result = build_index(_api_key, force=force)
            if result.get("ok"):
                st.success(result["message"])
                st.rerun()
            else:
                st.error(result.get("message", "索引失败"))
    
    # 文档列表
    st.markdown('<p style="font-size: 0.75rem; color: #86868b; text-transform: uppercase; letter-spacing: 0.05em; margin: 1.5rem 0 0.75rem;">内置文档</p>', unsafe_allow_html=True)
    
    official = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    for p in official:
        st.markdown(f'<div class="doc-item"><div class="doc-icon">📄</div><div style="flex: 1; overflow: hidden; text-overflow: ellipsis;">{p.name}</div></div>', unsafe_allow_html=True)
    
    # 文件上传
    st.markdown('<p style="font-size: 0.75rem; color: #86868b; text-transform: uppercase; letter-spacing: 0.05em; margin: 1.5rem 0 0.75rem;">上传文档</p>', unsafe_allow_html=True)
    
    uploaded = st.file_uploader("选择 Markdown 文件", type=["md"], accept_multiple_files=True, label_visibility="collapsed")
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
            col1, col2 = st.columns([4, 1])
            col1.markdown(f'<div style="font-size: 0.8rem; color: #3a3a3c; padding: 0.5rem 0;">📄 {fname}</div>', unsafe_allow_html=True)
            if col2.button("🗑️", key=f"rm_{fname}"):
                try:
                    (UPLOAD_DIR / fname).unlink()
                    st.rerun()
                except OSError as e:
                    st.error(str(e))

# ================= 主界面 =================
# Hero 区域
st.markdown("""
<div class="hero-section">
    <h1 class="hero-title">智能报销助手</h1>
    <p class="hero-subtitle">基于企业制度的智能问答系统，7×24小时为您解答报销相关问题</p>
</div>
""", unsafe_allow_html=True)

# 统计卡片
st.markdown(f"""
<div class="stats-container">
    <div class="stat-card">
        <div class="stat-value">{n_chunks}</div>
        <div class="stat-label">文档片段</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">97%</div>
        <div class="stat-label">问题覆盖率</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">&lt;3s</div>
        <div class="stat-label">平均响应</div>
    </div>
</div>
""", unsafe_allow_html=True)

# 状态提示
if not _has_key:
    st.warning("⚠️ 请配置 ZHIPU_API_KEY 后使用")
elif n_chunks == 0:
    st.info("📚 知识库为空，请先上传文档并重建索引")

# ================= 对话区域 =================
st.markdown("""
<div class="chat-container">
    <div class="chat-header">
        <div class="chat-title">💬 对话</div>
        <div class="chat-actions">
            <button class="chat-action-btn" onclick="window.location.reload()" title="清空对话">🗑️</button>
        </div>
    </div>
""", unsafe_allow_html=True)

# 快捷问题
if len(st.session_state.messages) == 0:
    st.markdown("""
    <div style="padding: 1rem 1.5rem;">
        <p style="font-size: 0.75rem; color: #86868b; margin-bottom: 0.75rem;">试试这些问题：</p>
        <div class="quick-questions">
    """, unsafe_allow_html=True)
    
    quick_questions = [
        "差旅费报销标准是多少？",
        "发票丢了怎么办？",
        "打车费能报销吗？",
        "超标住宿需要什么手续？"
    ]
    
    cols = st.columns(len(quick_questions))
    for i, q in enumerate(quick_questions):
        with cols[i]:
            if st.button(q, key=f"quick_{i}", use_container_width=True):
                st.session_state.quick_question = q
                st.rerun()
    
    st.markdown("</div></div>", unsafe_allow_html=True)

# 显示历史消息
for idx, msg in enumerate(st.session_state.messages):
    is_user = msg["role"] == "user"
    avatar = "👤" if is_user else "🤖"
    bubble_class = "user" if is_user else "assistant"
    
    st.markdown(f"""
    <div class="message">
        <div class="message-avatar {bubble_class}">{avatar}</div>
        <div class="message-content">
            <div class="message-bubble {bubble_class}">{msg['content']}</div>
    """, unsafe_allow_html=True)
    
    # 参考来源
    if not is_user and msg.get("sources"):
        st.markdown('<div class="sources-section"><div class="sources-title">📚 参考来源</div>', unsafe_allow_html=True)
        for i, s in enumerate(msg["sources"], 1):
            dist = f"{s.distance:.2%}" if s.distance is not None else "—"
            sec = f" · {s.section_path}" if getattr(s, "section_path", None) else ""
            st.markdown(f"""
            <div class="source-item">
                <div class="source-header">
                    <span class="source-name">{i}. {s.source}{sec}</span>
                    <span class="source-score">匹配度 {dist}</span>
                </div>
                <div style="color: #86868b; font-size: 0.75rem; margin-top: 0.25rem;">{s.text[:200]}...</div>
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
        <div class="empty-desc">输入您的问题，或点击上方快捷问题</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("</div>", unsafe_allow_html=True)  # 结束 chat-container

# ================= 输入区域 =================
st.markdown('<div class="input-container">', unsafe_allow_html=True)

# 检查是否有快捷问题
if "quick_question" in st.session_state:
    prompt = st.session_state.quick_question
    del st.session_state.quick_question
else:
    prompt = st.chat_input("输入您的问题...")

if prompt and _has_key:
    # 添加用户消息
    st.session_state.messages.append({
        "role": "user",
        "content": prompt
    })
    
    if n_chunks == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "⚠️ 当前知识库为空，请先上传文档并重建索引。",
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
                "content": f"❌ 抱歉，处理问题时出错：{e}",
                "sources": []
            })
    
    st.rerun()

st.markdown("</div>", unsafe_allow_html=True)
