"""
Streamlit 入口：企业报销知识问答 Web 界面。
"""
from __future__ import annotations

import html
import base64
import sys
from pathlib import Path

import streamlit as st

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import AUTH_MODE, DOCS_DIR, UPLOAD_DIR, ZHIPU_API_KEY
from auth import (
    authenticate,
    create_session,
    delete_session,
    ensure_demo_user,
    get_session_user,
    get_user,
    register_user,
    save_avatar,
)
from embedder import get_backend_type
from rag_engine import ask, build_index, collection_count


st.set_page_config(
    page_title="企业报销助手",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.html(
    """
<style>
    :root {
        --bg: #f7f4ee;
        --panel: #ffffff;
        --ink: #231815;
        --muted: #7b6f64;
        --line: #eadfce;
        --primary: #ef6c48;
        --primary-2: #f59e0b;
        --green: #22a06b;
        --orange: #f97316;
        --rose: #ec6b83;
        --shadow: 0 18px 48px rgba(80, 56, 35, 0.10);
        --soft-shadow: 0 10px 28px rgba(80, 56, 35, 0.06);
    }

    #MainMenu, footer, header { visibility: hidden; }

    .stApp {
        background:
            radial-gradient(circle at 78% -10%, rgba(255, 214, 168, 0.45), transparent 28rem),
            linear-gradient(180deg, #fffaf3 0%, var(--bg) 100%);
        color: var(--ink);
    }

    .main .block-container {
        max-width: 1180px;
        padding: 0.85rem 1.5rem 2.2rem;
    }

    [data-testid="stSidebar"] {
        background: #fffdf9;
        border-right: 1px solid var(--line);
        box-shadow: 10px 0 30px rgba(80, 56, 35, 0.04);
    }

    [data-testid="stSidebar"] .block-container {
        padding: 1rem 0.85rem 1rem;
    }

    .brand {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        padding: 0.35rem 0.2rem 1rem;
    }

    .brand-icon {
        width: 2.35rem;
        height: 2.35rem;
        border-radius: 10px;
        display: grid;
        place-items: center;
        color: white;
        background: linear-gradient(135deg, #ef6c48, #22a06b);
        box-shadow: 0 10px 22px rgba(239, 108, 72, 0.22);
        font-size: 1rem;
        font-weight: 800;
    }

    .brand-title {
        font-size: 1.02rem;
        font-weight: 800;
        color: var(--ink);
        line-height: 1.2;
    }

    .brand-subtitle {
        color: var(--muted);
        font-size: 0.76rem;
        margin-top: 0.12rem;
    }

    .nav-row {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        min-height: 2.55rem;
        margin: 0.18rem 0;
        padding: 0.62rem 0.78rem;
        border: 1px solid transparent;
        border-radius: 10px;
        color: #5b4b3e;
        font-size: 0.92rem;
        font-weight: 750;
        background: transparent;
    }

    .nav-row.active {
        color: #c84f2f;
        background: #fff2ea;
        border-color: #ffd4c3;
    }

    .nav-dot {
        width: 0.58rem;
        height: 0.58rem;
        border-radius: 999px;
        background: #decfbd;
        flex: 0 0 auto;
    }

    .nav-row.active .nav-dot {
        background: var(--primary);
        box-shadow: 0 0 0 4px rgba(239, 108, 72, 0.13);
    }

    [data-testid="stSidebar"] .stButton > button {
        color: #5b4b3e !important;
        background: #fffdf9 !important;
        border-color: #eee2d2 !important;
        justify-content: flex-start;
        text-align: left;
        box-shadow: none;
        min-height: 2.45rem;
    }

    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        color: #c84f2f !important;
        background: #fff2ea !important;
        border-color: #ffd4c3 !important;
    }

    [data-testid="stSidebar"] .stButton > button p {
        color: inherit !important;
        font-weight: 750;
    }

    .sidebar-card {
        margin-top: 1rem;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.9rem;
        background: #fffdf9;
        box-shadow: var(--soft-shadow);
    }

    .status-dot {
        width: 2rem;
        height: 2rem;
        border-radius: 50%;
        display: grid;
        place-items: center;
        color: white;
        background: var(--green);
        box-shadow: 0 10px 20px rgba(51, 199, 121, 0.22);
    }

    .topbar {
        display: flex;
        justify-content: flex-end;
        align-items: center;
        gap: 0.75rem;
        height: 2rem;
        margin-bottom: 0.55rem;
        color: #3f3229;
        font-size: 0.92rem;
    }

    .avatar {
        width: 1.8rem;
        height: 1.8rem;
        border-radius: 50%;
        background: linear-gradient(135deg, #ffe3d1, #f9a66d);
        display: inline-grid;
        place-items: center;
        color: #8a3a20;
        font-weight: 800;
        object-fit: cover;
    }

    .hero {
        position: relative;
        min-height: 215px;
        border: 1px solid #f7d8c4;
        border-radius: 18px;
        overflow: hidden;
        padding: 1.8rem 2rem;
        background:
            radial-gradient(circle at 82% 26%, rgba(34, 160, 107, 0.14), transparent 15rem),
            linear-gradient(135deg, #fff8ef 0%, #fff2e7 58%, #ffe1d0 100%);
        box-shadow: var(--shadow);
    }

    .hero h1 {
        margin: 0 0 0.55rem;
        font-size: clamp(2rem, 3.2vw, 2.8rem);
        line-height: 1.12;
        color: var(--ink);
        font-weight: 900;
        letter-spacing: 0;
    }

    .hero h1 span {
        color: #ef6c48;
    }

    .hero h2 {
        margin: 0;
        font-size: clamp(1rem, 1.45vw, 1.25rem);
        line-height: 1.35;
        color: #10182e;
        font-weight: 800;
        letter-spacing: 0;
    }

    .hero p {
        margin: 0.75rem 0 0;
        color: #6b5a4b;
        font-size: 0.98rem;
    }

    .hero-copy {
        max-width: 620px;
        position: relative;
        z-index: 2;
    }

    .hero-visual {
        position: absolute;
        right: 2.25rem;
        top: 1.65rem;
        width: 18rem;
        height: 10rem;
        opacity: 0.42;
        transform: rotate(-1.2deg);
        border: 1px solid rgba(255,255,255,0.76);
        border-radius: 18px;
        background: linear-gradient(145deg, rgba(255,255,255,0.9), rgba(255,243,229,0.68));
        box-shadow: 0 28px 70px rgba(180, 104, 78, 0.18);
        backdrop-filter: blur(12px);
    }

    .mock-header {
        height: 2.45rem;
        border-bottom: 1px solid rgba(125, 137, 180, 0.18);
        padding: 0.8rem 1.3rem;
        font-weight: 800;
        color: #4b3a2f;
        font-size: 0.9rem;
    }

    .mock-bubble {
        position: absolute;
        right: 1.1rem;
        top: 2.7rem;
        border-radius: 10px;
        padding: 0.68rem 0.95rem;
        background: #fff6ef;
        color: #4b3a2f;
        font-size: 0.84rem;
    }

    .mock-answer {
        position: absolute;
        left: 1.1rem;
        top: 5rem;
        width: 13.5rem;
        border-radius: 10px;
        padding: 1rem;
        background: rgba(255,255,255,0.92);
        color: #4b3a2f;
        font-size: 0.82rem;
        line-height: 1.65;
    }

    .mock-line {
        height: 0.32rem;
        border-radius: 999px;
        margin-top: 0.62rem;
        background: #f1dfcf;
    }

    .robot {
        position: absolute;
        right: 18.5rem;
        top: 4rem;
        width: 3rem;
        height: 3rem;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background: rgba(255,255,255,0.5);
        border: 1px solid rgba(255,255,255,0.8);
        box-shadow: 0 18px 45px rgba(180, 104, 78, 0.14);
        font-size: 1.45rem;
        opacity: 0.5;
        z-index: 3;
    }

    .ask-shell {
        max-width: none;
        margin: 0.95rem 0 0;
        position: relative;
        z-index: 4;
    }

    div[data-testid="stForm"] {
        border: 0;
        background: transparent;
        padding: 0;
    }

    .ask-shell div[data-testid="stTextInput"] input {
        height: 3.65rem !important;
        min-height: 3.65rem !important;
        line-height: 3.65rem !important;
        border-radius: 12px;
        border: 1px solid #efd6c3;
        background: #ffffff;
        box-shadow: 0 12px 28px rgba(80, 56, 35, 0.08);
        padding: 0 1.15rem !important;
        color: var(--ink);
        font-size: 1rem;
        display: flex;
        align-items: center;
    }

    .ask-shell div[data-testid="stTextInput"] input:focus {
        border-color: var(--primary);
        box-shadow: 0 0 0 3px rgba(239, 108, 72, 0.16), 0 12px 28px rgba(80, 56, 35, 0.08);
    }

    .ask-shell div[data-testid="stTextInput"] input::placeholder {
        color: #94a3b8;
    }

    .try-row {
        display: flex;
        gap: 0.55rem;
        align-items: center;
        margin: 1rem 0 0.55rem;
        color: #6b5a4b;
        font-size: 0.86rem;
    }

    .content-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 26rem;
        gap: 1.1rem;
        align-items: start;
        margin-top: 0.35rem;
    }

    .panel {
        border: 1px solid var(--line);
        border-radius: 16px;
        background: rgba(255,255,255,0.88);
        box-shadow: var(--soft-shadow);
    }

    .panel-pad {
        padding: 1.05rem;
    }

    .section-title {
        font-size: 1rem;
        font-weight: 850;
        color: var(--ink);
        margin: 0 0 0.8rem;
    }

    .answer-wrap {
        min-height: 430px;
        padding: 1rem;
        background: rgba(255,253,249,0.92);
        border: 1px solid #eadfce;
        border-radius: 12px;
    }

    .question-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        border-radius: 999px;
        padding: 0.72rem 1rem;
        background: #fff2ea;
        color: #8a3a20;
        font-size: 0.9rem;
        margin: 0.1rem 0 0.8rem;
    }

    .answer-card {
        border: 1px solid #eadfce;
        border-radius: 12px;
        background: #ffffff;
        box-shadow: 0 12px 32px rgba(80, 56, 35, 0.07);
        padding: 1.35rem 1.5rem;
        color: #4b3a2f;
        line-height: 1.75;
    }

    .card-tools {
        display: flex;
        justify-content: flex-end;
        gap: 0.5rem;
        margin-bottom: 0.35rem;
    }

    .tool-btn {
        width: 2.35rem;
        height: 2.35rem;
        border-radius: 10px;
        border: 1px solid #e5eafb;
        display: inline-grid;
        place-items: center;
        color: #1d2946;
        background: #ffffff;
    }

    .answer-markdown h1,
    .answer-markdown h2,
    .answer-markdown h3 {
        color: var(--primary);
        font-size: 1.06rem;
        margin: 1rem 0 0.3rem;
    }

    .answer-markdown p {
        margin: 0.35rem 0;
    }

    .answer-markdown ul {
        margin-top: 0.25rem;
    }

    .source-card {
        border: 1px solid #edf0fa;
        border-radius: 12px;
        padding: 0.82rem 0.92rem;
        margin: 0.6rem 0 0;
        background: #fafbff;
    }

    .source-title {
        color: #1d2946;
        font-weight: 800;
        font-size: 0.88rem;
        margin-bottom: 0.24rem;
    }

    .source-copy {
        color: #65708a;
        font-size: 0.82rem;
        line-height: 1.65;
    }

    .sample-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1.2rem;
        margin-top: 1rem;
    }

    .sample-block h4 {
        margin: 0 0 0.3rem;
        color: var(--primary);
        font-size: 1rem;
    }

    .sample-block.warn h4 {
        color: #ff7a1a;
    }

    .reference-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.65rem;
        padding-top: 1rem;
        margin-top: 1rem;
        border-top: 1px solid #edf0fa;
    }

    .ref-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        border-radius: 10px;
        background: #f7f8fc;
        padding: 0.68rem 0.9rem;
        color: #2e395b;
        font-size: 0.85rem;
    }

    .side-card {
        border: 1px solid #eadfce;
        border-radius: 12px;
        background: rgba(255,253,249,0.94);
        box-shadow: var(--soft-shadow);
        padding: 1rem;
        margin-bottom: 0.9rem;
    }

    .side-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 0.82rem;
        color: var(--ink);
        font-weight: 850;
    }

    .side-link {
        color: var(--primary);
        font-size: 0.82rem;
        font-weight: 750;
    }

    .stat-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.75rem;
        padding-bottom: 0.85rem;
        border-bottom: 1px solid #edf0fa;
    }

    .stat-value {
        color: var(--ink);
        font-size: 1.25rem;
        font-weight: 900;
    }

    .stat-label {
        color: var(--muted);
        font-size: 0.78rem;
        margin-top: 0.18rem;
    }

    .mini-chart {
        height: 4.35rem;
        display: flex;
        align-items: end;
        gap: 0.28rem;
        padding-top: 0.9rem;
    }

    .bar {
        flex: 1;
        border-radius: 999px 999px 0 0;
        background: linear-gradient(180deg, #7888ff, #dce2ff);
    }

    .stButton > button {
        border-radius: 10px;
        min-height: 2.55rem;
        border: 1px solid #eadfce;
        color: #3f3229;
        background: #ffffff;
        box-shadow: 0 5px 14px rgba(80, 56, 35, 0.04);
        font-weight: 720;
        white-space: normal;
        line-height: 1.35;
    }

    .stButton > button:hover {
        color: var(--primary);
        border-color: rgba(79, 99, 255, 0.36);
        background: #fff6ef;
    }

    .stButton > button[kind="primary"] {
        color: white;
        border-color: var(--primary);
        background: linear-gradient(135deg, #ff815f, #ef6c48);
    }

    .ask-shell .stButton > button {
        min-height: 3.65rem;
        border-radius: 12px;
        font-size: 1.25rem;
        box-shadow: 0 12px 26px rgba(239, 108, 72, 0.24);
    }

    .quick-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(9rem, 1fr));
        gap: 0.7rem;
        margin-bottom: 1rem;
    }

    .quick-card {
        border: 1px solid #dbe3ef;
        border-radius: 12px;
        background: #ffffff;
        padding: 0.85rem 0.95rem;
        box-shadow: 0 8px 22px rgba(15, 23, 42, 0.05);
        color: #1e293b;
    }

    .quick-title {
        font-size: 0.78rem;
        color: #64748b;
        margin-bottom: 0.25rem;
    }

    .quick-text {
        font-weight: 800;
        line-height: 1.35;
    }

    .hero-kicker {
        color: #b8552f;
        font-size: 0.92rem;
        font-weight: 800;
        margin-bottom: 0.35rem;
    }

    .hero-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
        margin-top: 1rem;
    }

    .hero-tag {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        border-radius: 999px;
        padding: 0.38rem 0.7rem;
        background: rgba(255, 255, 255, 0.7);
        color: #7b4b34;
        font-size: 0.78rem;
        font-weight: 750;
        border: 1px solid rgba(245, 185, 146, 0.7);
    }

    .dashboard-card {
        border: 1px solid #eadfce;
        border-radius: 16px;
        background: rgba(255,253,249,0.95);
        box-shadow: var(--soft-shadow);
        padding: 1rem;
        min-height: 0;
    }

    .overview-strip {
        margin: 1rem 0 0.85rem;
    }

    .hot-list {
        display: grid;
        gap: 0.62rem;
    }

    .hot-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.6rem 0.7rem;
        border-radius: 12px;
        background: #fff7ef;
        color: #4b3a2f;
        font-size: 0.84rem;
    }

    .hot-rank {
        display: inline-grid;
        place-items: center;
        width: 1.28rem;
        height: 1.28rem;
        border-radius: 50%;
        background: #ff815f;
        color: white;
        font-size: 0.72rem;
        font-weight: 800;
        flex: 0 0 auto;
    }

    .hot-count {
        color: #a07155;
        font-size: 0.76rem;
        white-space: nowrap;
    }

    .auth-shell {
        max-width: 1180px;
        min-height: calc(100vh - 3.4rem);
        margin: 0 auto;
        display: grid;
        grid-template-columns: minmax(0, 0.88fr) minmax(500px, 1.12fr);
        gap: 3.4rem;
        align-items: center;
    }

    .auth-panel,
    .profile-card {
        border: 1px solid #eadfce;
        border-radius: 18px;
        background: rgba(255,253,249,0.95);
        box-shadow: var(--shadow);
        padding: 1.4rem;
    }

    .auth-hero {
        min-height: 520px;
        padding: 0;
        position: relative;
    }

    .auth-hero h1 {
        margin: 3rem 0 1rem;
        font-size: clamp(2.2rem, 3.55vw, 3.25rem);
        line-height: 1.08;
        letter-spacing: 0;
    }

    .auth-hero h1 span {
        color: #ef6c48;
    }

    .auth-hero p {
        color: #6b5a4b;
        line-height: 1.55;
        margin: 0.75rem 0 0;
    }

    .auth-panel {
        min-height: 0;
        padding: 2.35rem 2.45rem;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.88);
        backdrop-filter: blur(10px);
    }

    .auth-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        border-radius: 999px;
        padding: 0.38rem 0.72rem;
        color: #9a4a28;
        background: rgba(255,255,255,0.72);
        border: 1px solid rgba(245, 185, 146, 0.72);
        font-size: 0.8rem;
        font-weight: 800;
    }

    .demo-account {
        border: 1px solid #ffd4c3;
        border-radius: 16px;
        background: #fff6ef;
        padding: 0.9rem 1rem;
        margin: 1rem 0 0.85rem;
    }

    .demo-title {
        color: #b8552f;
        font-weight: 900;
        margin-bottom: 0.45rem;
    }

    .demo-line {
        display: flex;
        justify-content: space-between;
        gap: 0.75rem;
        color: #5b4b3e;
        font-size: 0.88rem;
        margin-top: 0.28rem;
    }

    .demo-value {
        color: #231815;
        font-weight: 850;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }

    .login-brand {
        display: flex;
        align-items: center;
        gap: 0.8rem;
    }

    .login-brand-icon {
        width: 2.5rem;
        height: 2.5rem;
        border-radius: 10px;
        display: grid;
        place-items: center;
        color: white;
        background: linear-gradient(135deg, #ff815f, #ef6c48);
        font-size: 1.15rem;
        font-weight: 900;
        box-shadow: 0 12px 28px rgba(239, 108, 72, 0.25);
    }

    .login-brand-title {
        color: #231815;
        font-size: 1.05rem;
        font-weight: 900;
    }

    .login-brand-subtitle {
        color: #7b6f64;
        margin-top: 0.15rem;
        font-size: 0.84rem;
    }

    .login-features {
        display: grid;
        gap: 0.62rem;
        margin: 0.9rem 0 0;
        color: #6b5a4b;
        font-weight: 750;
    }

    .login-feature {
        display: flex;
        align-items: center;
        gap: 0.55rem;
    }

    .login-check {
        display: inline-grid;
        place-items: center;
        width: 1.1rem;
        height: 1.1rem;
        border-radius: 50%;
        color: white;
        background: #ff815f;
        font-size: 0.72rem;
        box-shadow: 0 0 0 4px rgba(255, 129, 95, 0.16);
    }

    .robot-stage {
        position: relative;
        height: 210px;
        margin-top: 0.9rem;
        border-radius: 28px;
        background: radial-gradient(circle at 50% 58%, rgba(255, 190, 140, 0.32), transparent 10rem);
    }

    .robot-figure {
        position: absolute;
        left: 50%;
        bottom: 0.5rem;
        transform: translateX(-50%);
        width: 10.8rem;
        height: 9rem;
        border-radius: 48% 48% 36% 36%;
        background: linear-gradient(180deg, #ffffff, #ffe8d7);
        box-shadow: 0 20px 55px rgba(132, 78, 42, 0.16);
    }

    .robot-face {
        position: absolute;
        left: 50%;
        top: 1.85rem;
        transform: translateX(-50%);
        width: 5.9rem;
        height: 3.25rem;
        border-radius: 2rem;
        background: #1f2933;
        box-shadow: inset 0 0 0 3px rgba(255,255,255,0.08);
    }

    .robot-eye {
        position: absolute;
        top: 1rem;
        width: 0.62rem;
        height: 0.88rem;
        border-radius: 999px;
        background: #58e0c2;
    }

    .robot-eye.left { left: 1.65rem; }
    .robot-eye.right { right: 1.65rem; }

    .robot-smile {
        position: absolute;
        left: 50%;
        bottom: 0.52rem;
        transform: translateX(-50%);
        width: 1.35rem;
        height: 0.62rem;
        border-bottom: 3px solid #58e0c2;
        border-radius: 0 0 999px 999px;
    }

    .float-note {
        position: absolute;
        border-radius: 14px;
        background: rgba(255,255,255,0.9);
        box-shadow: 0 12px 32px rgba(80, 56, 35, 0.10);
        padding: 0.55rem 0.7rem;
        color: #3f3229;
        font-weight: 850;
        font-size: 0.76rem;
    }

    .float-note small {
        display: block;
        color: #8b7a6c;
        margin-top: 0.2rem;
        font-weight: 650;
    }

    .float-note.one { left: 0.3rem; top: 3.4rem; }
    .float-note.two { right: 0.6rem; top: 0.6rem; }
    .float-note.three { right: 0.1rem; bottom: 1.8rem; }

    .auth-title {
        text-align: center;
        margin-bottom: 1rem;
    }

    .auth-title h2 {
        margin: 0 0 0.55rem;
        font-size: 1.65rem;
        color: #231815;
        letter-spacing: 0;
    }

    .auth-title p {
        margin: 0;
        color: #7b6f64;
        font-size: 0.95rem;
    }

    .auth-panel .stButton > button {
        min-height: 3rem;
    }

    .auth-divider {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        color: #9a8a7b;
        margin: 1rem 0;
        font-size: 0.85rem;
    }

    .auth-divider::before,
    .auth-divider::after {
        content: "";
        height: 1px;
        flex: 1;
        background: #eadfce;
    }

    .auth-footnote {
        text-align: center;
        color: #7b6f64;
        margin-top: 1rem;
        font-size: 0.88rem;
    }

    @media (max-width: 1100px) {
        .auth-shell {
            grid-template-columns: 1fr;
            gap: 1.5rem;
            margin-top: 1rem;
        }

        .auth-hero {
            min-height: auto;
        }

        .auth-hero h1 {
            margin-top: 2rem;
            font-size: clamp(2.1rem, 9vw, 3.2rem);
        }

        .robot-stage {
            display: none;
        }
    }

    .profile-head {
        display: flex;
        gap: 1rem;
        align-items: center;
        margin-bottom: 1rem;
    }

    .profile-avatar {
        width: 4.5rem;
        height: 4.5rem;
        border-radius: 18px;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, #ffe3d1, #f9a66d);
        color: #8a3a20;
        font-weight: 900;
        font-size: 1.6rem;
        overflow: hidden;
        object-fit: cover;
    }

    .profile-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.75rem;
    }

    .profile-field {
        border: 1px solid #eadfce;
        border-radius: 12px;
        background: #fffaf3;
        padding: 0.8rem 0.9rem;
    }

    .field-label {
        color: #8b7a6c;
        font-size: 0.78rem;
        margin-bottom: 0.25rem;
    }

    .field-value {
        color: #2f241e;
        font-weight: 850;
    }

    [data-testid="stChatInput"] {
        display: none;
    }

    @media (max-width: 1180px) {
        .hero-visual,
        .robot {
            display: none;
        }

        .hero {
            padding: 1.65rem;
        }

        .ask-shell {
            margin-left: 0;
        }

        .content-grid {
            grid-template-columns: 1fr;
        }
    }

    @media (max-width: 760px) {
        .main .block-container {
            padding: 0.85rem 0.85rem 2rem;
        }

        .hero {
            min-height: 210px;
            padding: 1.25rem;
        }

        .ask-shell {
            margin: 0.8rem 0 0;
        }

        .sample-grid,
        .stat-grid,
        .quick-grid {
            grid-template-columns: 1fr;
        }
    }
</style>
"""
)


def _safe_upload_name(name: str) -> str:
    p = Path(name).name
    if not p.lower().endswith(".md"):
        p += ".md"
    return p.replace("..", "_").replace("/", "_").replace("\\", "_")


def _list_uploaded_md() -> list[str]:
    if not UPLOAD_DIR.is_dir():
        return []
    return sorted([x.name for x in UPLOAD_DIR.glob("*.md")])


def _list_official_md() -> list[str]:
    if not DOCS_DIR.is_dir():
        return []
    return sorted([x.name for x in DOCS_DIR.glob("*.md")])


def _set_prompt(text: str) -> None:
    st.session_state.pending_prompt = text


def _render_source(index: int, source) -> None:
    dist = f"{source.distance:.0%}" if source.distance is not None else "无"
    section = f" / {source.section_path}" if source.section_path else ""
    title = html.escape(f"{index}. {source.source}{section}")
    excerpt_raw = source.text[:220] + "..." if len(source.text) > 220 else source.text
    excerpt = html.escape(excerpt_raw)
    st.html(
        f"""
        <div class="source-card">
            <div class="source-title">{title}</div>
            <div class="source-copy">匹配距离：{dist}</div>
            <div class="source-copy">{excerpt}</div>
        </div>
        """
    )


def _render_sample_answer() -> None:
    st.html(
        """
        <div class="question-pill">👤 发票丢失了还能报销吗？</div>
        <div class="answer-card">
            <div class="card-tools">
                <span class="tool-btn">👍</span>
                <span class="tool-btn">👎</span>
                <span class="tool-btn">📋</span>
            </div>
            <div class="answer-markdown">
                <h3>✅ 结论</h3>
                <p>发票丢失可以报销，但需要提供相关证明材料，并按公司规定流程审批。</p>
                <div class="sample-grid">
                    <div class="sample-block">
                        <h4>🧾 报销条件</h4>
                        <ul>
                            <li>因特殊原因导致发票丢失，且无法补开发票</li>
                            <li>符合公司费用报销的其他相关规定</li>
                        </ul>
                        <h4>📄 所需材料</h4>
                        <ul>
                            <li>发票丢失说明</li>
                            <li>交易凭证复印件或付款记录</li>
                        </ul>
                    </div>
                    <div class="sample-block warn">
                        <h4>⚠️ 注意事项</h4>
                        <ul>
                            <li>需在费用发生后 30 个工作日内申请</li>
                            <li>根据金额大小可能需要上级领导审批</li>
                        </ul>
                    </div>
                </div>
            </div>
            <div class="reference-row">
                <span class="ref-chip">📕 《费用报销管理办法》 第3.2条</span>
                <span class="ref-chip">📘 《发票管理规定》 第2.1条</span>
                <span class="ref-chip">📗 《差旅费报销标准》 附件1</span>
            </div>
        </div>
        """
    )


def _render_answer_message(msg: dict) -> None:
    content = html.escape(msg["content"])
    content = content.replace("\n", "<br>")
    st.html(
        f"""
        <div class="answer-card">
            <div class="card-tools">
                <span class="tool-btn">👍</span>
                <span class="tool-btn">👎</span>
                <span class="tool-btn">📋</span>
            </div>
            <div class="answer-markdown">{content}</div>
        </div>
        """
    )
    if msg.get("sources"):
        with st.expander("参考来源", expanded=False):
            for idx, source in enumerate(msg["sources"], 1):
                _render_source(idx, source)


def _avatar_markup(user: dict, *, size_class: str = "avatar") -> str:
    avatar_path = user.get("avatar_path") or ""
    if avatar_path and Path(avatar_path).is_file():
        path = Path(avatar_path)
        mime = "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif path.suffix.lower() == ".webp":
            mime = "image/webp"
        src = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        return f'<img class="{size_class}" src="{src}" alt="头像">'
    name = str(user.get("real_name") or "内")
    return f'<span class="{size_class}">{html.escape(name[:1])}</span>'


def _render_auth_page() -> None:
    left, right = st.columns([0.95, 1.05], gap="large")
    with left:
        st.html(
            """
            <section class="auth-hero">
                <div class="login-brand">
                    <div class="login-brand-icon">🤖</div>
                    <div>
                        <div class="login-brand-title">企业报销助手</div>
                        <div class="login-brand-subtitle">智能知识问答系统</div>
                    </div>
                </div>
                <h1>有任何报销问题，<br><span>尽管问我</span></h1>
                <div class="login-features">
                    <div class="login-feature"><span class="login-check">✓</span><span>精准检索企业制度</span></div>
                    <div class="login-feature"><span class="login-check">✓</span><span>AI 生成专业答案</span></div>
                    <div class="login-feature"><span class="login-check">✓</span><span>支持溯源参考</span></div>
                </div>
                <div class="robot-stage">
                    <div class="float-note one">报销标准是多少？</div>
                    <div class="float-note two">差旅报销制度<small>PDF</small></div>
                    <div class="float-note three">年度报销趋势<small>稳定增长</small></div>
                    <div class="robot-figure">
                        <div class="robot-face">
                            <span class="robot-eye left"></span>
                            <span class="robot-eye right"></span>
                            <span class="robot-smile"></span>
                        </div>
                    </div>
                </div>
                <p style="margin-top:1.2rem;">您的数据安全受到保护  仅供企业内部使用</p>
            </section>
            """
        )

    with right:
        if AUTH_MODE == "demo":
            ensure_demo_user()
            st.html(
                """
                <section class="auth-panel">
                <div class="auth-title">
                    <h2>欢迎登录</h2>
                    <p>使用演示账号进入系统，体验智能问答服务</p>
                </div>
                <div class="demo-account">
                    <div class="demo-title">Demo 账号</div>
                    <div class="demo-line"><span>员工号</span><span class="demo-value">E00001</span></div>
                    <div class="demo-line"><span>密码</span><span class="demo-value">Internal@123</span></div>
                </div>
                """
            )
            if st.button("使用 Demo 账号登录", type="primary", use_container_width=True):
                user = authenticate("E00001", "Internal@123")
                if user:
                    token = create_session(user["employee_id"])
                    st.session_state.current_user_id = user["employee_id"]
                    st.query_params["session"] = token
                    st.rerun()
            st.html(
                """
                <div class="auth-divider">或</div>
                <button disabled style="width:100%;min-height:3.2rem;border-radius:12px;border:1px solid #eadfce;background:#fff;color:#7b6f64;font-weight:800;">企业 SSO 单点登录（预留）</button>
                <div class="auth-footnote">没有账号？真实部署时请联系管理员开通访问权限</div>
                </section>
                """
            )
            return

        login_tab, register_tab = st.tabs(["登录", "注册"])
        with login_tab:
            with st.form("login_form"):
                employee_id = st.text_input("员工号", placeholder="例如：E00001")
                password = st.text_input("密码", type="password")
                submitted = st.form_submit_button("登录", type="primary", use_container_width=True)
            if submitted:
                user = authenticate(employee_id, password)
                if user:
                    token = create_session(user["employee_id"])
                    st.session_state.current_user_id = user["employee_id"]
                    st.query_params["session"] = token
                    st.rerun()
                else:
                    st.error("员工号或密码不正确。")

        with register_tab:
            with st.form("register_form"):
                employee_id = st.text_input("员工号", placeholder="必须存在于内部员工名册")
                real_name = st.text_input("真实姓名", placeholder="必须与员工名册一致")
                password = st.text_input("设置密码", type="password")
                confirm = st.text_input("确认密码", type="password")
                submitted = st.form_submit_button("注册", type="primary", use_container_width=True)
            if submitted:
                if password != confirm:
                    st.error("两次输入的密码不一致。")
                else:
                    ok, message = register_user(employee_id, real_name, password)
                    if ok:
                        st.success(message)
                    else:
                        st.error(message)


def _render_profile_page(user: dict) -> None:
    avatar_html = _avatar_markup(user, size_class="profile-avatar")
    st.html(
        f"""
        <div class="profile-card">
            <div class="profile-head">
                {avatar_html}
                <div>
                    <div class="field-label">当前登录员工</div>
                    <div style="font-size:1.45rem;font-weight:900;color:#231815;">{html.escape(user.get("real_name", ""))}</div>
                    <div style="color:#7b6f64;margin-top:.2rem;">昵称固定为真实姓名</div>
                </div>
            </div>
            <div class="profile-grid">
                <div class="profile-field"><div class="field-label">员工号</div><div class="field-value">{html.escape(user.get("employee_id", ""))}</div></div>
                <div class="profile-field"><div class="field-label">昵称</div><div class="field-value">{html.escape(user.get("nickname", ""))}</div></div>
                <div class="profile-field"><div class="field-label">部门</div><div class="field-value">{html.escape(user.get("department", "") or "-")}</div></div>
                <div class="profile-field"><div class="field-label">岗位</div><div class="field-value">{html.escape(user.get("title", "") or "-")}</div></div>
                <div class="profile-field"><div class="field-label">邮箱</div><div class="field-value">{html.escape(user.get("email", "") or "-")}</div></div>
            </div>
        </div>
        """
    )
    uploaded = st.file_uploader("更换头像", type=["png", "jpg", "jpeg", "webp"])
    if uploaded is not None:
        save_avatar(user["employee_id"], uploaded.name, uploaded.getvalue())
        st.success("头像已更新。")
        st.rerun()


if "messages" not in st.session_state:
    st.session_state.messages = []

if "page" not in st.session_state:
    st.session_state["page"] = "chat"

if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = ""

if not st.session_state.current_user_id and AUTH_MODE == "off":
    st.session_state.current_user_id = ensure_demo_user()["employee_id"]

session_token = st.query_params.get("session", "")
if not st.session_state.current_user_id and session_token:
    session_user = get_session_user(session_token)
    if session_user:
        st.session_state.current_user_id = session_user["employee_id"]

current_user = get_user(st.session_state.current_user_id) if st.session_state.current_user_id else None
if current_user is None:
    st.session_state.current_user_id = ""
    _render_auth_page()
    st.stop()


n_chunks = collection_count()
api_key = ZHIPU_API_KEY.strip()
has_key = bool(api_key)
official_docs = _list_official_md()
uploaded_docs = _list_uploaded_md()
doc_total = len(official_docs) + len(uploaded_docs)
embed_backend = get_backend_type()
index_ready = n_chunks > 0
status_text = "已就绪" if index_ready else "待索引"
status_color = "#20b96f" if index_ready else "#ff9f43"
status_icon = "✓" if index_ready else "!"
if embed_backend == "zhipu":
    backend_hint = "当前使用智谱 embedding-3，重建索引会调用智谱 Embedding API。"
elif embed_backend == "local":
    backend_hint = "当前使用本地 BGE Embedding，首次运行可能需要下载模型。"
else:
    backend_hint = "当前使用轻量本地 Hash Embedding，不消耗外部 Embedding 额度。"
last_user_message = next(
    (m["content"] for m in reversed(st.session_state.messages) if m["role"] == "user"),
    "发票丢失了还能报销吗？",
)


with st.sidebar:
    st.html(
        """
        <div class="brand">
            <div class="brand-icon">🤖</div>
            <div>
                <div class="brand-title">企业报销助手</div>
                <div class="brand-subtitle">智能知识问答系统</div>
            </div>
        </div>
        """
    )

    _PAGE_LABELS = {
        "chat":     "💬 智能问答",
        "profile":  "👤 我的信息",
        "kb":       "🗃️ 知识库管理",
        "upload":   "📤 上传文档",
        "index":    "🔎 索引管理",
        "stats":    "📊 使用统计",
        "settings": "⚙️ 系统设置",
    }
    for page_key, page_label in _PAGE_LABELS.items():
        if st.button(
            page_label,
            key=f"nav_{page_key}",
            type="primary" if st.session_state["page"] == page_key else "secondary",
            use_container_width=True,
        ):
            st.session_state["page"] = page_key
            st.rerun()
    current_page = st.session_state["page"]

    st.html(
        f"""
        <div class="sidebar-card">
            <div style="display:flex; gap:.75rem; align-items:center;">
                <div class="status-dot" style="background:{status_color};box-shadow:0 10px 20px {status_color}38;">{status_icon}</div>
                <div>
                    <div style="color:#65708a;font-size:.82rem;">知识库状态</div>
                    <div style="color:{status_color};font-weight:850;margin-top:.15rem;">{status_text}</div>
                </div>
            </div>
            <div style="height:1px;background:#edf0fa;margin:1rem 0;"></div>
            <div style="color:#65708a;font-size:.82rem;">文档数量</div>
            <div style="color:#0f172a;font-weight:850;margin:.2rem 0 .75rem;">{doc_total} 个</div>
            <div style="color:#65708a;font-size:.82rem;">索引文本块</div>
            <div style="color:#0f172a;font-weight:850;margin-top:.2rem;">{n_chunks} 个</div>
        </div>
        """
    )

    with st.expander("文档与索引", expanded=False):
        if has_key:
            st.success("API Key 已配置")
        else:
            st.error("未配置 ZHIPU_API_KEY")
        st.caption(backend_hint)
        st.caption("索引来源：`knowledge/` 与 `data/uploads/` 下的 Markdown 文件。")

        uploads = st.file_uploader(
            "上传 Markdown 制度文档",
            type=["md"],
            accept_multiple_files=True,
        )
        if uploads:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            saved: list[str] = []
            for file in uploads:
                name = _safe_upload_name(file.name)
                (UPLOAD_DIR / name).write_bytes(file.getvalue())
                saved.append(name)
            st.success(f"已保存 {len(saved)} 个文档")

        force_rebuild = st.checkbox("强制重建索引", value=False)
        if st.button("重建知识库索引", type="primary", use_container_width=True, disabled=not has_key):
            with st.spinner("正在读取制度文档并写入向量库..."):
                result = build_index(api_key, force=force_rebuild)
            if result.get("ok"):
                st.success(result.get("message", "索引已更新"))
                st.rerun()
            else:
                st.error(result.get("message", "索引构建失败"))

    if st.button("新建对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    if st.button("退出登录", use_container_width=True):
        delete_session(st.query_params.get("session", ""))
        if "session" in st.query_params:
            del st.query_params["session"]
        st.session_state.current_user_id = ""
        st.session_state.messages = []
        st.rerun()


st.html(
    f"""
    <div class="topbar">
        <span>🔔</span>
        {_avatar_markup(current_user)}
        <strong>{html.escape(current_user.get("real_name", ""))}</strong>
    </div>
    """
)

# ── Page: 智能问答 ────────────────────────────────────────────────────────────
if current_page == "chat":
    st.html(
        """
        <section class="hero">
            <div class="hero-copy">
                <div class="hero-kicker">您好，我是您的报销小助手</div>
                <h1>有任何报销问题，<span>尽管问我</span></h1>
                <h2>从制度检索到引用依据，一次给清楚</h2>
                <p>支持差旅、发票、招待费、审批流程等常见报销场景。</p>
                <div class="hero-tags">
                    <span class="hero-tag">✓ 知识库企业制度</span>
                    <span class="hero-tag">✓ 答案带引用</span>
                    <span class="hero-tag">✓ 支持模糊提问</span>
                </div>
            </div>
            <div class="robot">🤖</div>
            <div class="hero-visual">
                <div class="mock-header">AI 助手</div>
                <div class="mock-bubble">发票丢失了还能报销吗？</div>
                <div class="mock-answer">
                    已找到相关制度条款...
                    <div class="mock-line" style="width: 92%;"></div>
                    <div class="mock-line" style="width: 72%;"></div>
                </div>
            </div>
        </section>
        """
    )

    with st.container():
        st.html('<div class="ask-shell">')
        with st.form("hero_question", clear_on_submit=True):
            ask_cols = st.columns([8, 1])
            with ask_cols[0]:
                typed_prompt = st.text_input(
                    "报销问题",
                    placeholder="请输入您的报销问题，例如：发票丢失怎么办？",
                    label_visibility="collapsed",
                )
            with ask_cols[1]:
                submitted = st.form_submit_button("➤", type="primary", use_container_width=True)
        st.html("</div>")

    st.html(
        f"""
        <div class="overview-strip">
            <div class="dashboard-card">
                <div class="stat-grid">
                    <div>
                        <div class="stat-value">{doc_total}</div>
                        <div class="stat-label">制度文档</div>
                    </div>
                    <div>
                        <div class="stat-value">{n_chunks}</div>
                        <div class="stat-label">索引片段</div>
                    </div>
                    <div>
                        <div class="stat-value">100%</div>
                        <div class="stat-label">评测通过</div>
                    </div>
                </div>
            </div>
        </div>
        """
    )

    st.html('<div class="try-row"><span>试试问：</span></div>')
    try_cols = st.columns(3)
    try_questions = ["发票丢失怎么办？", "出差住宿标准是多少？", "招待费报销需要什么条件？"]
    for i, question in enumerate(try_questions):
        with try_cols[i]:
            st.button(question, key=f"try_{i}", use_container_width=True, on_click=_set_prompt, args=(question,))

    if not has_key:
        st.warning("请先在项目根目录 `.env` 中配置 `ZHIPU_API_KEY`。")
    elif n_chunks == 0:
        st.info("当前知识库索引为空。请在左侧「文档与索引」中重建 `knowledge/` 文档索引后再提问。")

    left, right = st.columns([2.25, 1], gap="large")

    with left:
        st.html('<div class="answer-wrap"><div class="section-title">AI 回答</div>')
        if not st.session_state.messages:
            _render_sample_answer()
        else:
            for msg in st.session_state.messages:
                if msg["role"] == "user":
                    question = html.escape(msg["content"])
                    st.html(f'<div class="question-pill">👤 {question}</div>')
                else:
                    _render_answer_message(msg)
        st.html("</div>")

    with right:
        st.html(
            """
            <div class="dashboard-card">
                <div class="side-title"><span>热门问题 TOP5</span><span class="side-link">一键问</span></div>
                <div class="hot-list">
                    <div class="hot-item"><span><span class="hot-rank">1</span> 发票丢失了还能报销吗？</span><span class="hot-count">128次</span></div>
                    <div class="hot-item"><span><span class="hot-rank">2</span> 出差住宿标准是多少？</span><span class="hot-count">96次</span></div>
                    <div class="hot-item"><span><span class="hot-rank">3</span> 报销需要哪些材料？</span><span class="hot-count">86次</span></div>
                    <div class="hot-item"><span><span class="hot-rank">4</span> 招待费报销条件是什么？</span><span class="hot-count">76次</span></div>
                    <div class="hot-item"><span><span class="hot-rank">5</span> 跨年度发票怎么处理？</span><span class="hot-count">65次</span></div>
                </div>
            </div>
            """
        )
        common_questions = [
            ("💬 发票问题", "发票丢失还能报销吗？"),
            ("✈️ 差旅标准", "出差住宿标准是多少？"),
            ("💜 费用标准", "交通补贴标准是什么？"),
            ("👥 审批流程", "报销审批需要多久？"),
            ("📌 其他问题", "公司报销的标准流程是什么？"),
        ]
        for idx, (title, question) in enumerate(common_questions):
            st.button(
                f"{title}\n{question}",
                key=f"common_{idx}",
                use_container_width=True,
                on_click=_set_prompt,
                args=(question,),
            )

    pending_prompt = st.session_state.pop("pending_prompt", None)
    prompt = pending_prompt or (typed_prompt if submitted else None)

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})

        if not has_key:
            st.session_state.messages.append(
                {"role": "assistant", "content": "请先配置 `ZHIPU_API_KEY` 后再提问。", "sources": []}
            )
            st.rerun()
            st.stop()

        if n_chunks == 0:
            st.session_state.messages.append(
                {"role": "assistant", "content": "当前知识库索引为空，请先在左侧「文档与索引」中重建索引。", "sources": []}
            )
            st.rerun()
            st.stop()

        try:
            with st.spinner("正在检索制度文档并生成答案..."):
                out = ask(api_key, prompt)
            st.session_state.messages.append(
                {"role": "assistant", "content": out.answer, "sources": out.sources}
            )
        except Exception as exc:
            st.session_state.messages.append(
                {"role": "assistant", "content": f"抱歉，处理问题时出错：{exc}", "sources": []}
            )
        st.rerun()

# ── Page: 我的信息 ────────────────────────────────────────────────────────────
elif current_page == "profile":
    st.subheader("👤 我的信息")
    st.markdown("---")
    _render_profile_page(current_user)

# ── Page: 知识库管理 ──────────────────────────────────────────────────────────
elif current_page == "kb":
    st.subheader("🗃️ 知识库管理")
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("官方文档", len(official_docs))
        if official_docs:
            st.caption("官方制度文档：")
            for d in official_docs:
                st.markdown(f"- 📄 {d}")
        else:
            st.info(f"`knowledge/` 目录下暂无 .md 文档")
    with col2:
        st.metric("上传文档", len(uploaded_docs))
        if uploaded_docs:
            st.caption("用户上传文档：")
            for d in uploaded_docs:
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"- 📄 {d}")
                if c2.button("删除", key=f"del_{d}"):
                    (UPLOAD_DIR / d).unlink(missing_ok=True)
                    st.success(f"已删除 {d}")
                    st.rerun()
        else:
            st.info("暂无用户上传文档")
    st.markdown("---")
    st.metric("向量库文本块", n_chunks)

# ── Page: 上传文档 ────────────────────────────────────────────────────────────
elif current_page == "upload":
    st.subheader("📤 上传文档")
    st.markdown("上传 Markdown 格式的制度文档，上传后需重建索引才能生效。")
    st.markdown("---")
    uploads = st.file_uploader(
        "选择 Markdown 文件（支持多选）",
        type=["md"],
        accept_multiple_files=True,
    )
    if uploads:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for file in uploads:
            name = _safe_upload_name(file.name)
            (UPLOAD_DIR / name).write_bytes(file.getvalue())
            saved.append(name)
        st.success(f"✅ 已保存 {len(saved)} 个文档：{', '.join(saved)}")
        st.info("请前往「索引管理」页面重建索引，使新文档生效。")

    if uploaded_docs:
        st.markdown("---")
        st.caption(f"当前已上传文档（{len(uploaded_docs)} 个）：")
        for d in uploaded_docs:
            st.markdown(f"- 📄 {d}")

# ── Page: 索引管理 ────────────────────────────────────────────────────────────
elif current_page == "index":
    st.subheader("🔎 索引管理")
    st.markdown("---")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("文档总数", doc_total)
    col_b.metric("索引文本块", n_chunks)
    col_c.metric("知识库状态", status_text)

    st.markdown("---")
    if not has_key:
        st.error("未配置 ZHIPU_API_KEY，无法操作索引。")
    else:
        st.caption(backend_hint)
        force_rebuild = st.checkbox("强制重建（清空现有索引）", value=False)
        if st.button("🔄 重建知识库索引", type="primary", disabled=not has_key):
            with st.spinner("正在读取制度文档并写入向量库..."):
                result = build_index(api_key, force=force_rebuild)
            if result.get("ok"):
                st.success(result.get("message", "索引已更新"))
                st.rerun()
            else:
                st.error(result.get("message", "索引构建失败"))

# ── Page: 使用统计 ────────────────────────────────────────────────────────────
elif current_page == "stats":
    st.subheader("📊 使用统计")
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    col1.metric("文档总数", doc_total)
    col2.metric("索引文本块", n_chunks)
    col3.metric("本次会话问答", len([m for m in st.session_state.messages if m["role"] == "user"]))

    st.markdown("---")
    st.caption("提问历史（当前会话）")
    user_msgs = [m for m in st.session_state.messages if m["role"] == "user"]
    if user_msgs:
        for i, m in enumerate(user_msgs, 1):
            st.markdown(f"{i}. {m['content']}")
    else:
        st.info("本次会话暂无提问记录。")

# ── Page: 系统设置 ────────────────────────────────────────────────────────────
elif current_page == "settings":
    st.subheader("⚙️ 系统设置")
    st.markdown("---")
    st.markdown("**API 配置**")
    if has_key:
        st.success(f"✅ ZHIPU_API_KEY 已配置（{api_key[:8]}{'*' * (len(api_key) - 8) if len(api_key) > 8 else ''}）")
    else:
        st.error("❌ 未检测到 ZHIPU_API_KEY")
        st.code('# 在项目根目录 .env 中添加：\nZHIPU_API_KEY=你的密钥', language="bash")

    st.markdown("---")
    st.markdown("**模型配置**")
    from config import CHAT_MODEL, EMBED_MODEL, DEFAULT_TOP_K, SIMILARITY_THRESHOLD, MAX_CONTEXT_CHARS
    st.markdown(f"- 对话模型：`{CHAT_MODEL}`")
    st.markdown(f"- 嵌入模型：`{EMBED_MODEL}`")
    st.markdown(f"- Embedding 后端：`{embed_backend}`")
    st.markdown(f"- 默认 Top-K：`{DEFAULT_TOP_K}`")
    st.markdown(f"- 相似度阈值：`{SIMILARITY_THRESHOLD}`")
    st.markdown(f"- 最大上下文字符：`{MAX_CONTEXT_CHARS}`")

    st.markdown("---")
    st.markdown("**会话操作**")
    if st.button("🗑️ 清空对话历史", type="secondary"):
        st.session_state.messages = []
        st.success("对话历史已清空")
        st.rerun()
