"""
Whimsy & Delight Engine — 趣味体验核心模块。

为费用报销 RAG 问答系统注入品牌个性、加载趣味、成就系统和 Easter eggs。
设计原则：
- 企业场景适度活泼，不干扰核心任务
- 加载态用温暖替代等待焦虑
- 成就系统激励探索而非制造压力
- 全部元素有 `aria` 标注，支持 reduced motion

品牌角色：小财（Xiao Cai）—— 一个幽默但不失专业的报销政策助手。
"""

from __future__ import annotations

import random
from typing import Any, cast

import streamlit as st

# ---------------------------------------------------------------------------
# Brand Character: 小财
# ---------------------------------------------------------------------------
XIAOCAI_INTRO = "你好，我是小财，你的报销政策导航员。把问题丢过来，我帮你翻制度。"

XIAOCAI_GREETINGS = [
    "又见面了！今天有什么报销困惑？",
    "小财在线。差旅、招待、日常费用——随时问。",
    "报销新规记不住？没关系，我帮你记着。",
    "早上好！小财已加载最新政策，请随意提问。",
    "下午好！需要查什么政策？我翻书很快的。",
]

# ---------------------------------------------------------------------------
# Loading Microcopy — rotating whimsy
# ---------------------------------------------------------------------------
LOADING_SEQUENCES = [
    # Standard flow
    [
        ("翻阅报销制度中…", "正在检索相关条款"),
        ("逐条核对政策原文…", "确保引用准确无误"),
        ("整理回答要点…", "让你一目了然"),
    ],
    # Playful variant
    [
        ("启动政策扫描雷达…", "滴滴滴——发现相关条款"),
        ("戴上老花镜逐字核对…", "确保每一条都有据可查"),
        ("把专业术语翻译成人话…", "说清楚，不说废话"),
    ],
    # Warm variant
    [
        ("在政策库里仔细翻找…", "不放过任何一条相关条款"),
        ("跟财务部确认最新口径…", "保证给的是最新版本"),
        ("帮你把重点画出来…", "复杂的规定，简单的解释"),
    ],
    # Coffee variant
    [
        ("小财正在泡咖啡查阅政策…", "稍等，马上找到"),
        ("逐条比对中…", "这条适用，那条也看看"),
        ("最后检查一遍引用…", "确保万无一失"),
    ],
]

PUBLISH_LOADING = [
    "正在扫描文件结构…",
    "识别政策条款和章节…",
    "提取关键信息并建立索引…",
    "验证索引是否正确…",
    "准备发布，即将生效…",
]

EVAL_LOADING = [
    "准备测试题库…",
    "正在跑第一批检索…",
    "统计命中率和耗时…",
    "汇总对比结果…",
]

# ---------------------------------------------------------------------------
# Success Microcopy
# ---------------------------------------------------------------------------
ANSWER_SUCCESS = [
    "回答已就绪，请核对政策原文。",
    "小财觉得这个答案还行，你看看对不对？",
    "搞定！如有疑问，随时追问。",
    "以上回答基于现行政策，如有变更我会第一时间知道。",
]

PUBLISH_SUCCESS = [
    "新政策已生效，小财的知识库又更新了！",
    "发布成功。现在问答会引用最新版本了。",
    "政策库已刷新，小财的「脑容量」又增加了。",
]

FIRST_ANSWER_CELEBRATION = "🎉 第一次问答完成！小财正式上岗了。"
FIFTH_ANSWER_CELEBRATION = "🌟 第 5 次问答！看来小财还挺靠谱的？"
FIRST_PUBLISH_CELEBRATION = "📚 第一篇政策上线！小财的知识体系从今天开始积累。"
FIRST_EVAL_CELEBRATION = "🔬 第一次评测完成！数据不会说谎，小财会变得更好。"
FIRST_BADCASE_RESOLVED = "✅ 第一个问题已解决！持续改进，小财才会越来越聪明。"

# ---------------------------------------------------------------------------
# Error Microcopy — personality reduces frustration
# ---------------------------------------------------------------------------
ERROR_PERSONALITY = [
    ("小财打了个盹…", "服务暂时不可用，请稍等片刻再试。"),
    ("政策书掉地上了…", "网络似乎不太稳定，请检查连接后重试。"),
    ("小财的字典缺了一页…", "后端服务响应超时，已记录日志，请稍后重试。"),
    ("咖啡洒在键盘上了…", "服务异常，管理员已收到通知，请稍等。"),
]

# ---------------------------------------------------------------------------
# Empty State Microcopy
# ---------------------------------------------------------------------------
EMPTY_ASK_PROMPTS = [
    ("出差要准备哪些材料？", "差旅费报销需要准备哪些材料？"),
    ("发票抬头填错了还能报销吗？", "发票抬头填错了还能报销吗？应该怎么处理？"),
    ("超标准费用怎么审批？", "费用超过报销标准时，需要走什么审批流程？"),
]

# ---------------------------------------------------------------------------
# Achievement System
# ---------------------------------------------------------------------------
ACHIEVEMENTS = {
    "first_answer": {
        "icon": "🎉",
        "title": "初次见面",
        "description": "完成第一次政策问答。小财正式上岗！",
        "celebration": "balloons",
    },
    "5_answers": {
        "icon": "🌟",
        "title": "熟客驾到",
        "description": "完成 5 次问答。看来小财还挺靠谱的？",
        "celebration": "toast",
    },
    "20_answers": {
        "icon": "🏆",
        "title": "重度用户",
        "description": "完成 20 次问答。你已经离不开小财了。",
        "celebration": "confetti",
    },
    "first_publish": {
        "icon": "📚",
        "title": "知识播种者",
        "description": "发布第一篇政策文档。小财的知识从这里开始。",
        "celebration": "balloons",
    },
    "first_eval": {
        "icon": "🔬",
        "title": "数据侦探",
        "description": "完成第一次检索评测。用数据说话。",
        "celebration": "toast",
    },
    "first_badcase_resolved": {
        "icon": "✅",
        "title": "问题终结者",
        "description": "解决第一个 Bad Case。每一次修复都让系统更可靠。",
        "celebration": "toast",
    },
    "konami": {
        "icon": "🌈",
        "title": "彩蛋猎人",
        "description": "你发现了隐藏的彩虹模式！好奇心满分。",
        "celebration": "rainbow",
    },
    "logo_clicker": {
        "icon": "🖱️",
        "title": "坚持不懈",
        "description": "连点 logo 10 次。这个发现方式……真有你的。",
        "celebration": "snow",
    },
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_loading_copy(phase: int, total: int = 3) -> tuple[str, str]:
    """Return (heading, sub_text) for a loading phase.
    
    Randomly picks a sequence variant on first call, caches it for the session.
    """
    if "whimsy_loading_seq" not in st.session_state:
        st.session_state.whimsy_loading_seq = random.randint(0, len(LOADING_SEQUENCES) - 1)
    seq = LOADING_SEQUENCES[st.session_state.whimsy_loading_seq]
    if 0 <= phase < len(seq):
        return cast(tuple[str, str], seq[phase])
    return ("处理中…", "")


def get_random_error_personality() -> tuple[str, str]:
    """Return (title, detail) with a touch of personality."""
    return random.choice(ERROR_PERSONALITY)


def get_random_success(kind: str = "answer") -> str:
    """Return a random success message."""
    pool = {
        "answer": ANSWER_SUCCESS,
        "publish": PUBLISH_SUCCESS,
    }.get(kind, ANSWER_SUCCESS)
    return random.choice(pool)


def get_greeting() -> str:
    """Return a random 小财 greeting."""
    return random.choice(XIAOCAI_GREETINGS)


# ---------------------------------------------------------------------------
# Achievement Tracker
# ---------------------------------------------------------------------------

def track_answer_count() -> int:
    """Increment and return the total answer count for this session."""
    st.session_state.setdefault("whimsy_answer_count", 0)
    st.session_state.whimsy_answer_count += 1
    return int(st.session_state.whimsy_answer_count)


def check_and_celebrate_achievement(
    achievement_id: str, custom_message: str | None = None
) -> bool:
    """Check if *achievement_id* is newly unlocked; if so, celebrate and return True.

    Celebration types:
    - "balloons" → st.balloons()
    - "snow" → st.snow()
    - "confetti" → confetti CSS animation via st.markdown
    - "rainbow" → rainbow CSS effect
    - "toast" → st.toast()
    """
    st.session_state.setdefault("whimsy_achievements", set())
    unlocked = st.session_state.whimsy_achievements

    if achievement_id in unlocked:
        return False

    unlocked.add(achievement_id)
    achievement = ACHIEVEMENTS.get(achievement_id)
    if not achievement:
        return False

    msg = custom_message or f"{achievement['icon']} **{achievement['title']}** — {achievement['description']}"
    celebration = achievement["celebration"]

    if celebration == "balloons":
        st.balloons()
        st.toast(msg, icon=achievement["icon"])
    elif celebration == "snow":
        st.snow()
        st.toast(msg, icon=achievement["icon"])
    elif celebration == "confetti":
        _trigger_confetti()
        st.toast(msg, icon=achievement["icon"])
    elif celebration == "rainbow":
        _trigger_rainbow()
        st.toast(msg, icon=achievement["icon"])
    else:
        st.toast(msg, icon=achievement["icon"])

    return True


def _trigger_confetti() -> None:
    """Inject a lightweight confetti CSS animation overlay."""
    st.markdown(
        """<div class="whimsy-confetti" aria-hidden="true">
  <span style="--x:10;--d:1.2s">🎉</span><span style="--x:30;--d:1.5s">✨</span>
  <span style="--x:50;--d:1.0s">🌟</span><span style="--x:70;--d:1.8s">💫</span>
  <span style="--x:90;--d:1.3s">🎊</span>
</div>""",
        unsafe_allow_html=True,
    )


def _trigger_rainbow() -> None:
    """Inject a rainbow gradient background effect (auto-removes after 8s via JS)."""
    st.markdown(
        """<style id="whimsy-rainbow-style">
@keyframes whimsy-rainbow-bg {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}
.stApp {
  background: linear-gradient(
    270deg, #ff9a9e, #fecfef, #a1c4fd, #c2e9fb, #d4fc79, #96e6a1, #ff9a9e
  ) !important;
  background-size: 1400% 1400% !important;
  animation: whimsy-rainbow-bg 6s ease infinite !important;
}
</style>
<script>
setTimeout(function() {
  var el = document.getElementById('whimsy-rainbow-style');
  if (el) el.remove();
}, 8000);
</script>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Easter Egg: Logo Click Counter
# ---------------------------------------------------------------------------

def track_logo_click() -> int:
    """Track logo area clicks. Returns current count; triggers achievement at 10."""
    st.session_state.setdefault("whimsy_logo_clicks", 0)
    st.session_state.whimsy_logo_clicks += 1
    count = int(st.session_state.whimsy_logo_clicks)

    if count == 5:
        st.toast("👀 你在点什么呢？", icon="👀")
    elif count == 8:
        st.toast("快了…再点两下试试？", icon="🤫")
    elif count >= 10:
        check_and_celebrate_achievement("logo_clicker")
        st.session_state.whimsy_logo_clicks = 0

    return count


# ---------------------------------------------------------------------------
# Session Initialization
# ---------------------------------------------------------------------------

def init_session() -> None:
    """Initialize whimsy session state (call once in streamlit_app.py)."""
    st.session_state.setdefault("whimsy_answer_count", 0)
    st.session_state.setdefault("whimsy_achievements", set())
    st.session_state.setdefault("whimsy_logo_clicks", 0)
    st.session_state.setdefault("whimsy_first_visit", True)


def is_first_visit() -> bool:
    """Check and consume the first-visit flag."""
    if st.session_state.get("whimsy_first_visit", True):
        st.session_state.whimsy_first_visit = False
        return True
    return False


# ---------------------------------------------------------------------------
# Achievement Summary (for display in sidebar or settings)
# ---------------------------------------------------------------------------

def render_achievement_wall() -> None:
    """Render a small achievement showcase."""
    unlocked = st.session_state.get("whimsy_achievements", set())
    if not unlocked:
        st.caption("还没有解锁成就。多多使用小财吧！")
        return

    st.caption(f"已解锁 {len(unlocked)} / {len(ACHIEVEMENTS)} 个成就")
    for aid in unlocked:
        a = ACHIEVEMENTS.get(aid)
        if a:
            st.markdown(f"{a['icon']} **{a['title']}** — {a['description']}")


# ---------------------------------------------------------------------------
# Delightful Empty State — guided prompts with personality
# ---------------------------------------------------------------------------

def render_guided_prompts(on_click_callback: Any = None) -> None:
    """Render guided question buttons with 小财 personality."""
    with st.container(border=True):
        st.markdown("#### 💬 你可以这样问")
        st.caption("不需要记住制度名称，直接描述你的场景就好。")
        cols = st.columns(3)
        prompts = EMPTY_ASK_PROMPTS
        for i, (label, question) in enumerate(prompts):
            with cols[i]:
                icons = [":material/luggage:", ":material/receipt:", ":material/approval:"]
                st.button(
                    label,
                    icon=icons[i],
                    on_click=on_click_callback,
                    args=(question,) if on_click_callback else None,
                    use_container_width=True,
                    key=f"whimsy_prompt_{i}",
                )
