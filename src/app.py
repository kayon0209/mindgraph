"""
Streamlit 入口：企业报销知识问答 Web 界面（极简版）
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
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ================= 极简风格 CSS =================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');
    
    .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        background: #ffffff;
        color: #1a1a1a;
    }
    
    #MainMenu, footer, header {visibility: hidden;}
    
    /* 主容器 - 居中窄版 */
    .main .block-container {
        max-width: 768px;
        padding: 0 1rem;
    }
    
    /* 顶部导航 */
    .nav {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 60px;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(10px);
        border-bottom: 1px solid #f0f0f0;
        z-index: 100;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    
    .nav-inner {
        width: 100%;
        max-width: 768px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 1rem;
    }
    
    .nav-brand {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 1.125rem;
        font-weight: 600;
        color: #1a1a1a;
    }
    
    .nav-menu {
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .nav-btn {
        width: 36px;
        height: 36px;
        border-radius: 8px;
        border: none;
        background: transparent;
        color: #666;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.25rem;
        transition: all 0.2s;
    }
    
    .nav-btn:hover {
        background: #f5f5f5;
        color: #1a1a1a;
    }
    
    /* 主内容区 */
    .main-content {
        margin-top: 60px;
        min-height: calc(100vh - 60px);
        display: flex;
        flex-direction: column;
    }
    
    /* 欢迎区域 */
    .welcome {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 4rem 1rem;
        text-align: center;
    }
    
    .welcome-icon {
        width: 64px;
        height: 64px;
        background: linear-gradient(135deg, #10a37f 0%, #0d8c6d 100%);
        border-radius: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 2rem;
        margin-bottom: 1.5rem;
    }
    
    .welcome-title {
        font-size: 1.75rem;
        font-weight: 600;
        color: #1a1a1a;
        margin-bottom: 0.5rem;
    }
    
    .welcome-subtitle {
        font-size: 0.9375rem;
        color: #666;
        margin-bottom: 2rem;
    }
    
    /* 快捷问题 */
    .suggestions {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 0.5rem;
        max-width: 600px;
    }
    
    .suggestion-chip {
        padding: 0.625rem 1rem;
        background: #f7f7f8;
        border: 1px solid #e5e5e5;
        border-radius: 8px;
        font-size: 0.875rem;
        color: #374151;
        cursor: pointer;
        transition: all 0.2s;
    }
    
    .suggestion-chip:hover {
        background: #e8e8ea;
        border-color: #d1d1d1;
    }
    
    /* 对话区域 */
    .chat-container {
        flex: 1;
        padding: 1rem 0;
    }
    
    .message {
        display: flex;
        gap: 1rem;
        padding: 1.5rem 0;
        border-bottom: 1px solid #f0f0f0;
        animation: fadeIn 0.3s ease;
    }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .message-avatar {
        width: 32px;
        height: 32px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        flex-shrink: 0;
    }
    
    .message-avatar.user {
        background: #5436da;
    }
    
    .message-avatar.assistant {
        background: #10a37f;
    }
    
    .message-content {
        flex: 1;
        font-size: 0.9375rem;
        line-height: 1.6;
        color: #1a1a1a;
    }
    
    .message-content p {
        margin: 0 0 0.75rem;
    }
    
    .message-content p:last-child {
        margin-bottom: 0;
    }
    
    /* 参考来源 */
    .sources {
        margin-top: 1rem;
        padding: 0.875rem 1rem;
        background: #f9fafb;
        border-radius: 8px;
        border: 1px solid #e5e7eb;
    }
    
    .sources-title {
        font-size: 0.75rem;
        font-weight: 600;
        color: #6b7280;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.5rem;
    }
    
    .source-item {
        padding: 0.5rem 0;
        border-bottom: 1px solid #e5e7eb;
        font-size: 0.8125rem;
    }
    
    .source-item:last-child {
        border-bottom: none;
        padding-bottom: 0;
    }
    
    .source-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.25rem;
    }
    
    .source-name {
        font-weight: 500;
        color: #111827;
    }
    
    .source-score {
        font-size: 0.6875rem;
        color: #10a37f;
        background: rgba(16, 163, 127, 0.1);
        padding: 0.125rem 0.5rem;
        border-radius: 4px;
    }
    
    .source-text {
        color: #6b7280;
        font-size: 0.75rem;
        line-height: 1.5;
    }
    
    /* 输入区域 */
    .input-container {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        background: linear-gradient(to top, #fff 60%, transparent);
        padding: 1rem;
        z-index: 50;
    }
    
    .input-inner {
        max-width: 768px;
        margin: 0 auto;
    }
    
    .input-box {
        display: flex;
        align-items: flex-end;
        gap: 0.5rem;
        background: #fff;
        border: 1px solid #e5e5e5;
        border-radius: 12px;
        padding: 0.75rem;
        box-shadow: 0 2px 6px rgba(0,0,0,0.05);
    }
    
    .input-box:focus-within {
        border-color: #10a37f;
        box-shadow: 0 2px 6px rgba(16, 163, 127, 0.1);
    }
    
    /* 底部提示 */
    .footer-hint {
        text-align: center;
        font-size: 0.75rem;
        color: #9ca3af;
        padding: 0.5rem;
    }
    
    /* 侧边栏样式 */
    [data-testid="stSidebar"] {
        background: #fff !important;
        border-right: 1px solid #f0f0f0;
    }
    
    .sidebar-content {
        padding: 1.5rem;
    }
    
    .sidebar-title {
        font-size: 0.875rem;
        font-weight: 600;
        color: #1a1a1a;
        margin-bottom: 1rem;
    }
    
    .sidebar-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.75rem;
        border-radius: 8px;
        font-size: 0.875rem;
        color: #374151;
        cursor: pointer;
        transition: all 0.2s;
    }
    
    .sidebar-item:hover {
        background: #f9fafb;
    }
    
    .sidebar-item.active {
        background: #f3f4f6;
    }
    
    /* 空状态 */
    .empty-chat {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 4rem 1rem;
        color: #9ca3af;
    }
    
    .empty-icon {
        font-size: 3rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }
    
    /* 按钮覆盖 */
    .stButton > button {
        border-radius: 8px !important;
        font-weight: 500 !important;
        font-size: 0.875rem !important;
    }
    
    .stButton > button[kind="primary"] {
        background: #10a37f !important;
        border: none !important;
    }
    
    /* 隐藏 Streamlit 默认元素 */
    .stChatInputContainer {
        position: fixed !important;
        bottom: 0 !important;
        left: 50% !important;
        transform: translateX(-50%) !important;
        width: 100% !important;
        max-width: 768px !important;
        padding: 1rem !important;
        background: linear-gradient(to top, #fff 80%, transparent) !important;
        z-index: 100 !important;
    }
    
    /* 警告样式 */
    .stAlert {
        border-radius: 8px !important;
        border: none !important;
    }
</style>
""", unsafe_allow_html=True)

# ================= 初始化 =================
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


# ================= 数据 =================
n_chunks = collection_count()
_api_key = ZHIPU_API_KEY.strip()
_has_key = bool(_api_key)

# ================= 侧边栏（极简）====================
with st.sidebar:
    st.markdown('<div class="sidebar-content">', unsafe_allow_html=True)
    
    # 品牌
    st.markdown("""
    <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid #f0f0f0;">
        <div style="width: 36px; height: 36px; background: linear-gradient(135deg, #10a37f 0%, #0d8c6d 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 1.25rem;">💼</div>
        <div>
            <div style="font-weight: 600; color: #1a1a1a;">报销助手</div>
            <div style="font-size: 0.75rem; color: #9ca3af;">v5.0</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # 历史对话（模拟）
    st.markdown('<div class="sidebar-title">历史对话</div>', unsafe_allow_html=True)
    
    if len(st.session_state.messages) > 0:
        # 取第一条用户消息作为标题
        first_msg = next((m for m in st.session_state.messages if m["role"] == "user"), None)
        title = first_msg["content"][:20] + "..." if first_msg else "新对话"
        st.markdown(f"""
        <div class="sidebar-item active">
            <span>💬</span>
            <span>{title}</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="padding: 1rem; text-align: center; color: #9ca3af; font-size: 0.875rem;">
            暂无历史对话
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<div style='margin: 1.5rem 0; border-top: 1px solid #f0f0f0;'></div>", unsafe_allow_html=True)
    
    # 操作
    st.markdown('<div class="sidebar-title">操作</div>', unsafe_allow_html=True)
    
    if st.button("➕ 新建对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    
    if st.button("🗑️ 清空当前", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    
    st.markdown("<div style='margin: 1.5rem 0; border-top: 1px solid #f0f0f0;'></div>", unsafe_allow_html=True)
    
    # 关于
    st.markdown('<div class="sidebar-title">关于</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="font-size: 0.8125rem; color: #6b7280; line-height: 1.6;">
        <p>基于企业报销制度的智能问答系统</p>
        <p style="margin-top: 0.5rem;">
            <a href="https://github.com/pleaselikeme/expense-rag-qa" target="_blank" style="color: #10a37f; text-decoration: none;">GitHub →</a>
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)

# ================= 顶部导航 =================
st.markdown("""
<div class="nav">
    <div class="nav-inner">
        <div class="nav-brand">
            <span>💼</span>
            <span>报销助手</span>
        </div>
        <div class="nav-menu">
            <button class="nav-btn" onclick="window.location.reload()" title="新对话">➕</button>
            <button class="nav-btn" title="菜单" id="menu-btn">☰</button>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ================= 主内容 =================
st.markdown('<div class="main-content">', unsafe_allow_html=True)

# 状态提示
if not _has_key:
    st.warning("⚠️ 系统配置中，请稍后再试")
elif n_chunks == 0:
    st.info("📚 知识库准备中，请稍后再试")

# 空状态 - 显示欢迎
if len(st.session_state.messages) == 0:
    st.markdown("""
    <div class="welcome">
        <div class="welcome-icon">💼</div>
        <div class="welcome-title">有什么可以帮您的？</div>
        <div class="welcome-subtitle">我可以帮您查询企业报销相关政策和流程</div>
        <div class="suggestions">
    """, unsafe_allow_html=True)
    
    suggestions = [
        "差旅费报销标准是多少？",
        "发票丢了怎么办？",
        "打车费能报销吗？",
        "超标住宿需要什么手续？",
        "报销需要哪些材料？"
    ]
    
    cols = st.columns(3)
    for i, sug in enumerate(suggestions[:3]):
        with cols[i]:
            if st.button(sug, key=f"sug_{i}", use_container_width=True):
                st.session_state.quick_question = sug
                st.rerun()
    
    cols2 = st.columns(2)
    for i, sug in enumerate(suggestions[3:]):
        with cols2[i]:
            if st.button(sug, key=f"sug_{i+3}", use_container_width=True):
                st.session_state.quick_question = sug
                st.rerun()
    
    st.markdown("""
        </div>
    </div>
    """, unsafe_allow_html=True)

# 对话区域
else:
    st.markdown('<div class="chat-container">', unsafe_allow_html=True)
    
    for msg in st.session_state.messages:
        is_user = msg["role"] == "user"
        avatar = "👤" if is_user else "🤖"
        
        st.markdown(f"""
        <div class="message">
            <div class="message-avatar {'user' if is_user else 'assistant'}">{avatar}</div>
            <div class="message-content">{msg['content']}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # 参考来源
        if not is_user and msg.get("sources"):
            st.markdown('<div class="message"><div class="message-avatar" style="visibility: hidden;"></div><div class="message-content">', unsafe_allow_html=True)
            st.markdown('<div class="sources"><div class="sources-title">参考来源</div>', unsafe_allow_html=True)
            
            for i, s in enumerate(msg["sources"], 1):
                dist = f"{s.distance:.0%}" if s.distance is not None else "—"
                sec = f" · {s.section_path}" if getattr(s, "section_path", None) else ""
                st.markdown(f"""
                <div class="source-item">
                    <div class="source-header">
                        <span class="source-name">{i}. {s.name}{sec}</span>
                        <span class="source-score">匹配度 {dist}</span>
                    </div>
                    <div class="source-text">{s.text[:120]}...</div>
                </div>
                """, unsafe_allow_html=True)
            
            st.markdown('</div></div></div>', unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)

# 底部留白（给输入框）
st.markdown("<div style='height: 100px;'></div>", unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ================= 输入框 =================
if "quick_question" in st.session_state:
    prompt = st.session_state.quick_question
    del st.session_state.quick_question
else:
    prompt = st.chat_input("输入您的问题...")

if prompt and _has_key and n_chunks > 0:
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    try:
        with st.spinner(""):
            out = ask(_api_key, prompt)
        st.session_state.messages.append({
            "role": "assistant",
            "content": out.answer,
            "sources": out.sources
        })
    except Exception as e:
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"抱歉，处理问题时出错：{e}",
            "sources": []
        })
    st.rerun()

# 底部提示
st.markdown("""
<div style="position: fixed; bottom: 0; left: 0; right: 0; text-align: center; padding: 0.5rem; font-size: 0.75rem; color: #9ca3af; background: linear-gradient(to top, #fff 80%, transparent);">
    报销助手基于企业制度文档回答，仅供参考
</div>
""", unsafe_allow_html=True)
