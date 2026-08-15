"""
报销政策助手 — Streamlit 主入口。

变更摘要（生产级 UX 升级）：
- 自动跟随系统 Light/Dark 主题
- 全局 CSS 注入（badge / card / skeleton / pulse）
- 连接池复用的 API 客户端
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.api_client import APIClientError, ProductAPIClient
from ui.theme import inject_global_css
from ui.whimsy import (
    get_greeting,
    init_session,
    is_first_visit,
    render_achievement_wall,
    track_logo_click,
    XIAOCAI_INTRO,
)

# ---------------------------------------------------------------------------
# Page config — auto theme + branding
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="报销政策助手",
    page_icon=":material/receipt_long:",
    layout="centered",
)

# Inject shared CSS once at startup
inject_global_css()

# Initialize whimsy session state
init_session()


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------
@st.cache_resource
def get_api_client() -> ProductAPIClient:
    return ProductAPIClient()


st.session_state.setdefault("api_client", get_api_client())
st.session_state.setdefault("last_answer", None)
st.session_state.setdefault("chat_messages", [])
st.session_state.setdefault("answer_profile", None)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
page = st.navigation(
    {
        "": [
            st.Page("app_pages/ask.py", title="政策问答", icon=":material/forum:"),
            st.Page("app_pages/knowledge.py", title="政策中心", icon=":material/library_books:"),
        ],
        "质量运营": [
            st.Page("app_pages/evaluation.py", title="质量看板", icon=":material/monitoring:"),
            st.Page("app_pages/bad_cases.py", title="问题改进", icon=":material/task_alt:"),
        ],
    },
    position="top",
)

# ---------------------------------------------------------------------------
# Sidebar: Xiaocai brand + achievements (must be before page.run())
# ---------------------------------------------------------------------------
with st.sidebar:
    # Xiaocai brand badge — clickable Easter egg
    st.markdown(
        '<div class="whimsy-xiaocai-badge whimsy-wiggle" style="cursor:pointer;margin-bottom:12px">'
        '📋 小财 · 报销政策助手'
        '</div>',
        unsafe_allow_html=True,
    )
    if st.button("📋 关于小财", use_container_width=True,
                 help="点击了解小财，多次点击有惊喜", key="about_xiaocai"):
        track_logo_click()
        if is_first_visit():
            st.toast(get_greeting(), icon="📋")
            st.info(XIAOCAI_INTRO, icon="📋")
        else:
            st.info(XIAOCAI_INTRO, icon="📋")

    st.divider()
    st.caption("🏆 成就墙")
    render_achievement_wall()

page.run()
