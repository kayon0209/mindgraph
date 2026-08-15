"""
Shared theme, CSS, and reusable UI components for all Streamlit pages.

Provides:
- Light/Dark theme CSS injection (auto-detected from Streamlit theme)
- Standardised typography, spacing, badge, and card patterns
- Loading skeletons, progress indicators, error-recovery templates
- Retry wrapper for API calls
- Whimsy animations: confetti, floating emojis, achievement pop-in, xiaocai zone
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

import streamlit as st

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Colour tokens (light theme baseline; dark variants use CSS custom properties)
# ---------------------------------------------------------------------------
COLOURS = {
    "brand": "#A84528",
    "brand_light": "#F4E1D7",
    "success": "#356859",
    "success_light": "#DFEEE8",
    "info": "#315E74",
    "info_light": "#E0EBF0",
    "warning": "#C28A3D",
    "warning_light": "#FDF3E4",
    "danger": "#A13D3D",
    "danger_light": "#F3DEDA",
    "text": "#28231F",
    "text_secondary": "#736A60",
    "bg": "#FBF8F2",
    "bg_card": "#F1E9DC",
    "border": "#D8CCBC",
    "surface": "#FFFFFF",
}


def inject_global_css() -> None:
    """Inject shared CSS that adapts to Streamlit's light/dark theme.

    Call once at the top of `streamlit_app.py`.
    """
    st.markdown(
        """<style>
/* ===== Design Tokens ===== */
:root {
    --color-brand: #A84528;
    --color-brand-light: #F4E1D7;
    --color-success: #356859;
    --color-success-light: #DFEEE8;
    --color-info: #315E74;
    --color-info-light: #E0EBF0;
    --color-warning: #C28A3D;
    --color-warning-light: #FDF3E4;
    --color-danger: #A13D3D;
    --color-danger-light: #F3DEDA;

    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 16px;
    --space-lg: 24px;
    --space-xl: 32px;
    --space-2xl: 48px;

    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-xl: 24px;
    --radius-full: 9999px;

    --font-mono: 'IBM Plex Mono', 'Cascadia Code', 'Fira Code', monospace;

    --transition-fast: 150ms ease;
    --transition-smooth: 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

/* ===== Typography ===== */
.expense-heading {
    font-family: var(--font), 'Noto Serif SC', serif;
    letter-spacing: -0.02em;
}
.expense-body {
    line-height: 1.7;
    max-width: 68ch;
}

/* ===== Badge System ===== */
.expense-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 10px;
    border-radius: var(--radius-full);
    font-size: 0.8rem;
    font-weight: 500;
    line-height: 1.6;
    white-space: nowrap;
    transition: background var(--transition-fast);
}
.expense-badge--brand  { background: var(--color-brand-light);  color: var(--color-brand); }
.expense-badge--success { background: var(--color-success-light); color: var(--color-success); }
.expense-badge--info    { background: var(--color-info-light);    color: var(--color-info); }
.expense-badge--warning { background: var(--color-warning-light); color: var(--color-warning); }
.expense-badge--danger  { background: var(--color-danger-light);  color: var(--color-danger); }
.expense-badge--muted   { background: rgba(128,128,128,0.1);      color: var(--text-secondary); }

/* ===== Card System ===== */
.expense-card {
    border: 1px solid var(--border-color, rgba(128,128,128,0.15));
    border-radius: var(--radius-lg);
    padding: var(--space-lg);
    background: var(--background-color);
    transition: border-color var(--transition-smooth), box-shadow var(--transition-smooth);
}
.expense-card:hover {
    border-color: var(--primary-color);
    box-shadow: 0 2px 12px rgba(168,69,40,0.08);
}

/* ===== Metric Cards ===== */
[data-testid="stMetric"] {
    border: 1px solid var(--border-color, rgba(128,128,128,0.12));
    border-radius: var(--radius-md);
    padding: var(--space-md) !important;
    transition: border-color var(--transition-smooth);
}
[data-testid="stMetric"]:hover {
    border-color: var(--primary-color);
}

/* ===== Loading / Skeleton ===== */
@keyframes expense-shimmer {
    0%   { background-position: -400px 0; }
    100% { background-position: 400px 0; }
}
.expense-skeleton {
    background: linear-gradient(90deg,
        var(--secondary-background-color) 25%,
        var(--background-color) 50%,
        var(--secondary-background-color) 75%
    );
    background-size: 800px 100%;
    animation: expense-shimmer 1.8s ease-in-out infinite;
    border-radius: var(--radius-sm);
    height: 1em;
    margin-bottom: var(--space-sm);
}
.expense-skeleton--heading { height: 1.4em; width: 60%; }
.expense-skeleton--text    { height: 1em;   width: 85%; }

/* ===== Progress / Pulse ===== */
@keyframes expense-pulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.5; }
}
.expense-pulse {
    animation: expense-pulse 1.5s ease-in-out infinite;
}

/* ===== Focus Ring (Accessibility) ===== */
*:focus-visible {
    outline: 2px solid var(--primary-color) !important;
    outline-offset: 2px;
}

/* ===== Responsive tweaks ===== */
@media (max-width: 640px) {
    .expense-card { padding: var(--space-md); }
}

/* ===== Dark-mode overrides (automatically applied when Streamlit uses dark theme) ===== */
[data-theme="dark"] .expense-badge--brand  { background: rgba(168,69,40,0.2);  }
[data-theme="dark"] .expense-badge--success { background: rgba(53,104,89,0.2);  }
[data-theme="dark"] .expense-badge--info    { background: rgba(49,94,116,0.2);   }
[data-theme="dark"] .expense-badge--warning { background: rgba(194,138,61,0.2);  }
[data-theme="dark"] .expense-badge--danger  { background: rgba(161,61,61,0.2);   }

[data-theme="dark"] .expense-card:hover {
    box-shadow: 0 2px 16px rgba(168,69,40,0.15);
}

[data-theme="dark"] .expense-skeleton {
    background: linear-gradient(90deg,
        rgba(255,255,255,0.04) 25%,
        rgba(255,255,255,0.08) 50%,
        rgba(255,255,255,0.04) 75%
    );
    background-size: 800px 100%;
}

/* ===== Whimsy: Confetti Celebration ===== */
@keyframes whimsy-confetti-fall {
    0%   { transform: translateY(-120%) rotate(0deg) scale(1); opacity: 1; }
    100% { transform: translateY(100vh) rotate(720deg) scale(0.3); opacity: 0; }
}
.whimsy-confetti {
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    pointer-events: none;
    z-index: 9999;
    overflow: hidden;
}
.whimsy-confetti span {
    position: absolute;
    top: -5%;
    left: calc(var(--x) * 1%);
    font-size: 28px;
    animation: whimsy-confetti-fall var(--d, 2s) ease-in forwards;
    animation-delay: calc(var(--i, 0) * 0.2s);
}

/* ===== Whimsy: Achievement Pop-In ===== */
@keyframes whimsy-pop-in {
    0%   { transform: scale(0) rotate(-10deg); opacity: 0; }
    60%  { transform: scale(1.15) rotate(2deg); opacity: 1; }
    100% { transform: scale(1) rotate(0deg); }
}
.whimsy-achievement-toast {
    animation: whimsy-pop-in 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}

/* ===== Whimsy: Floating Emoji Background ===== */
@keyframes whimsy-float-up {
    0%   { transform: translateY(0) scale(0); opacity: 1; }
    50%  { opacity: 1; }
    100% { transform: translateY(-200px) scale(1.5); opacity: 0; }
}
.whimsy-float-emoji {
    position: fixed;
    pointer-events: none;
    z-index: 9998;
    font-size: 32px;
    animation: whimsy-float-up 2.5s ease-out forwards;
}

/* ===== Whimsy: Pulse Glow (for brand moments) ===== */
@keyframes whimsy-glow {
    0%, 100% { box-shadow: 0 0 8px rgba(168,69,40,0.3); }
    50%      { box-shadow: 0 0 24px rgba(168,69,40,0.6), 0 0 48px rgba(168,69,40,0.2); }
}
.whimsy-glow {
    animation: whimsy-glow 2s ease-in-out infinite;
}

/* ===== Whimsy: Typewriter Cursor ===== */
@keyframes whimsy-blink {
    0%, 50%  { opacity: 1; }
    51%, 100% { opacity: 0; }
}
.whimsy-cursor::after {
    content: '▌';
    animation: whimsy-blink 1s step-end infinite;
    color: var(--primary-color);
}

/* ===== Whimsy: Xiaocai Brand Zone ===== */
.whimsy-xiaocai-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 14px;
    border-radius: var(--radius-full);
    background: linear-gradient(135deg, var(--color-brand-light), #FDE8D0);
    color: var(--color-brand);
    font-size: 0.85rem;
    font-weight: 500;
    transition: all var(--transition-smooth);
}
.whimsy-xiaocai-badge:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 12px rgba(168,69,40,0.15);
}
[data-theme="dark"] .whimsy-xiaocai-badge {
    background: linear-gradient(135deg, rgba(168,69,40,0.25), rgba(168,69,40,0.1));
}

/* ===== Whimsy: Subtle Wiggle on Hover ===== */
@keyframes whimsy-wiggle {
    0%, 100% { transform: rotate(0deg); }
    25%      { transform: rotate(-2deg); }
    75%      { transform: rotate(2deg); }
}
.whimsy-wiggle:hover {
    animation: whimsy-wiggle 0.4s ease-in-out;
}

/* ===== Accessibility: Reduced Motion ===== */
@media (prefers-reduced-motion: reduce) {
    .whimsy-confetti span,
    .whimsy-float-emoji,
    .whimsy-achievement-toast,
    .whimsy-wiggle:hover {
        animation: none !important;
    }
    .whimsy-cursor::after {
        animation: none;
        content: '';
    }
}
</style>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# UI Component Helpers
# ---------------------------------------------------------------------------
def badge(label: str, variant: str = "brand") -> str:
    """Return an HTML badge string for use inside st.markdown(…, unsafe_allow_html=True).

    Variants: brand | success | info | warning | danger | muted
    """
    return f'<span class="expense-badge expense-badge--{variant}">{label}</span>'


def card(content: str) -> str:
    """Wrap content in a premium card container."""
    return f'<div class="expense-card">{content}</div>'


def skeleton_block(lines: int = 3, heading: bool = True) -> None:
    """Render a shimmer skeleton placeholder."""
    if heading:
        st.markdown(
            '<div class="expense-skeleton expense-skeleton--heading"></div>',
            unsafe_allow_html=True,
        )
    for _ in range(lines):
        st.markdown(
            '<div class="expense-skeleton expense-skeleton--text"></div>',
            unsafe_allow_html=True,
        )


def error_recovery(
    title: str = "服务暂时不可用",
    detail: str = "",
    retry_action: str | None = None,
    contact: str = "请联系系统管理员或在企业微信群反馈。",
) -> None:
    """Render a friendly error block with retry hint and contact info.

    Args:
        title: Short error heading.
        detail: Technical detail (shown collapsed).
        retry_action: If provided, the label of a retry button; a clickable
                      hint is rendered.
        contact: Contact / escalation message.
    """
    st.error(f"**{title}**", icon=":material/cloud_off:")
    st.caption("请检查网络连接后刷新页面，或稍等片刻再试。")
    if retry_action:
        if st.button(f"🔄 {retry_action}", type="secondary"):
            st.rerun()
    if contact:
        st.info(contact, icon=":material/support_agent:")
    if detail:
        with st.expander("技术详情", icon=":material/build:"):
            st.code(detail, language="text")


def retry_api(
    fn: Callable[[], T],
    max_retries: int = 2,
    backoff: float = 0.8,
    label: str = "操作",
) -> T:
    """Call *fn* with automatic retry on `APIClientError`.

    Shows a toast on the first retry, then raises on final failure.
    """
    from ui.api_client import APIClientError  # local import to avoid circular

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except APIClientError as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = backoff * (2**attempt)
                st.toast(f"{label}失败，{wait:.1f}s 后重试…（{attempt + 1}/{max_retries}）", icon=":material/refresh:")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def loading_progress(steps: list[str]) -> None:
    """Display a multi-step loading progress indicator.

    Args:
        steps: List of step descriptions. The *last* item is treated as
               the currently-active step; earlier items are shown as completed.
    """
    if not steps:
        return
    cols = st.columns(len(steps))
    for i, (col, step) in enumerate(zip(cols, steps)):
        with col:
            if i < len(steps) - 1:
                st.markdown(f":material/check_circle: *{step}*")
            else:
                st.markdown(
                    f'<span class="expense-pulse">:material/hourglass_top: **{step}**</span>',
                    unsafe_allow_html=True,
                )


def empty_state(
    icon: str = ":material/search_off:",
    title: str = "暂无数据",
    description: str = "",
    action_label: str | None = None,
    action_help: str | None = None,
) -> None:
    """Render a consistent empty-state placeholder."""
    with st.container(border=True):
        col1, col2 = st.columns([1, 8])
        with col1:
            st.markdown(f"<div style='font-size:2.5rem;text-align:center;padding-top:12px'>{icon}</div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"**{title}**")
            if description:
                st.caption(description)
            if action_label:
                st.caption(action_help or "")
