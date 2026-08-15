"""
政策问答页面 — 核心交互入口。

UX 升级：
- 流式回答进度从一行小灰字 → 多步骤进度条 + 动画脉冲
- 错误不再是"终止" → 可重试 + 降级提示 + 联系入口
- 首次加载用 skeleton 占位替代空白等待
"""
from datetime import date

import streamlit as st

from ui.api_client import APIClientError
from ui.theme import badge, error_recovery, skeleton_block, loading_progress, empty_state
from ui.whimsy import (
    check_and_celebrate_achievement,
    get_loading_copy,
    get_random_error_personality,
    get_random_success,
    render_guided_prompts,
    track_answer_count,
)

client = st.session_state.api_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def queue_question(question: str) -> None:
    st.session_state.pending_question = question


def profile_label(item: dict) -> str:
    provider = item.get("provider")
    if provider == "deepseek":
        return "快速回答"
    if provider == "zhipu":
        return "审慎回答（较慢）"
    return f"{provider} · {item.get('model')}"


def authority_label(value: str | None) -> str:
    return {
        "official_policy": "正式制度",
        "official_guideline": "官方指引",
        "approved_faq": "已审核问答",
        "user_uploaded_reference": "补充资料",
        "external_reference": "外部参考",
    }.get(value or "", "来源未标注")


def render_evidence(citations: list[dict]) -> None:
    if not citations:
        return
    with st.expander(f"查看政策依据（{len(citations)} 条）", icon=":material/gavel:"):
        for citation in citations:
            with st.container(border=True):
                st.markdown(f"**{citation['document_name']}**")
                detail_parts = filter(None, [
                    authority_label(citation.get("authority_level")),
                    f"版本 {citation['document_version']}" if citation.get("document_version") else None,
                    citation.get("section_path"),
                ])
                st.caption(" · ".join(detail_parts))
                st.write(citation["excerpt"])


def render_completed(result: dict) -> None:
    st.markdown(result["answer"])
    if result.get("degraded"):
        st.warning("本次回答使用了降级路径，请优先核对政策原文。", icon=":material/warning:")
    render_evidence(result.get("citations", []))
    with st.popover("回答信息", icon=":material/info:"):
        elapsed = result.get("timing", {}).get("total_ms")
        st.caption(
            f"回答耗时：{elapsed / 1000:.1f} 秒" if elapsed is not None else "回答耗时：未记录"
        )
        st.caption(f"政策版本：{result.get('index_version') or '未记录'}")
        st.caption(f"回答模型：{result.get('model') or '未记录'}")
        if result.get("retrieval_trace"):
            with st.expander("技术诊断", icon=":material/build:"):
                st.json(result["retrieval_trace"])


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("报销政策助手")
st.markdown(
    f"<span class='whimsy-xiaocai-badge'>📋 小财在线</span> 把报销问题说清楚，其余交给我。回答只引用已发布的公司政策，并附上可核对的原文。",
    unsafe_allow_html=True,
)

# --- Fetch config (with skeleton fallback) ---
config_placeholder = st.empty()
try:
    config = client.public_config()
    config_placeholder.empty()
except APIClientError as exc:
    config_placeholder.empty()
    error_recovery(
        title="政策服务暂时不可用",
        detail=str(exc),
        retry_action="重新连接",
    )
    st.stop()

available = [item for item in config.get("chat_models", []) if item.get("configured")]
if not available:
    st.warning(
        "回答模型暂不可用，但仍可检索政策依据。",
        icon=":material/warning:",
    )

# --- Empty-state guided prompts (whimsy-powered) ---
if not st.session_state.chat_messages:
    render_guided_prompts(on_click_callback=queue_question)

# --- Chat history ---
for message in st.session_state.chat_messages:
    avatar = (
        ":material/person:" if message["role"] == "user" else ":material/account_balance:"
    )
    with st.chat_message(message["role"], avatar=avatar):
        if message["role"] == "assistant":
            render_completed(message["result"])
        else:
            st.write(message["content"])

# --- Settings popover ---
with st.popover("回答设置", icon=":material/tune:"):
    st.caption("通常无需调整。历史政策查询或对响应速度有要求时再使用。")
    profile_options = available or [{"provider": None, "model": None}]
    default_provider = config.get("default_chat_provider")
    default_index = next(
        (i for i, item in enumerate(profile_options) if item.get("provider") == default_provider),
        0,
    )
    selected_profile = st.selectbox(
        "回答方式", profile_options, index=default_index, format_func=profile_label
    )
    strategy_labels = {
        "hybrid": "智能检索",
        "hybrid_rerank": "精细检索（更慢）",
        "dense": "语义检索",
        "bm25": "关键词检索",
    }
    strategy = st.selectbox(
        "检索方式",
        config.get("retrieval_strategies", ["hybrid"]),
        index=config.get("retrieval_strategies", ["hybrid"]).index(
            config.get("default_retrieval_strategy", "hybrid")
        ),
        format_func=lambda v: strategy_labels.get(v, v),
    )
    categories = st.multiselect(
        "政策范围", config.get("knowledge_categories", []), placeholder="全部政策"
    )
    historical = st.toggle("查询历史政策")
    use_query_date = st.toggle("指定政策日期")
    query_date = st.date_input("政策日期", value=date.today(), disabled=not use_query_date)

# --- Input ---
pending = st.session_state.pop("pending_question", None)
typed = st.chat_input("描述你的报销场景…", submit_mode="disable")
question = pending or typed

if question:
    st.session_state.chat_messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar=":material/person:"):
        st.write(question)

    with st.chat_message("assistant", avatar=":material/account_balance:"):
        # --- Improved: multi-step progress indicator with whimsy copy ---
        progress_steps = st.empty()
        steps_cols = st.columns(3)
        step_markers = [
            steps_cols[0].empty(),
            steps_cols[1].empty(),
            steps_cols[2].empty(),
        ]
        # Initial state: only step 1 is active
        step1_heading, step1_sub = get_loading_copy(0)
        step_markers[0].markdown(
            f'<span class="expense-pulse">:material/hourglass_top: **{step1_heading}**</span>',
            unsafe_allow_html=True,
        )
        step_markers[1].caption("整理答案")
        step_markers[2].caption("核对引用")
        # Progress bar
        progress_bar = st.progress(0, text=step1_sub)

        answer_slot = st.empty()
        text, completed, citations = "", None, []

        try:
            for event in client.stream_chat(
                {
                    "question": question,
                    "retrieval_strategy": strategy,
                    "chat_provider": selected_profile.get("provider"),
                    "chat_model": selected_profile.get("model"),
                    "final_top_k": 5,
                    "include_retrieval_trace": True,
                    "query_date": query_date.isoformat() if use_query_date else None,
                    "knowledge_categories": categories,
                    "include_historical": historical,
                }
            ):
                if event["event"] == "retrieval_completed":
                    # Step 1 → done, Step 2 → active (whimsy copy)
                    step_markers[0].markdown(":material/check_circle: *查找政策*")
                    step2_heading, step2_sub = get_loading_copy(1)
                    step_markers[1].markdown(
                        f'<span class="expense-pulse">:material/hourglass_top: **{step2_heading}**</span>',
                        unsafe_allow_html=True,
                    )
                    progress_bar.progress(40, text=step2_sub)
                elif event["event"] == "answer_delta":
                    text += event["data"]["text"]
                    answer_slot.markdown(text + " ▌")
                    step3_heading, step3_sub = get_loading_copy(2)
                    progress_bar.progress(
                        min(80, 40 + len(text) // 4), text=step3_sub
                    )
                elif event["event"] == "citations":
                    citations = event["data"]["citations"]
                elif event["event"] == "completed":
                    completed = event["data"]
                elif event["event"] == "error":
                    # --- Improved: error recovery with whimsy personality ---
                    progress_bar.empty()
                    for m in step_markers:
                        m.empty()
                    err_title, err_detail = get_random_error_personality()
                    st.error(f"**{err_title}**  {err_detail}", icon=":material/error:")
                    retry_key = f"retry_{hash(question)}"
                    if st.button("🔄 让小财再试一次", key=retry_key):
                        st.session_state.pending_question = question
                        st.rerun()
                    st.stop()
        except APIClientError:
            progress_bar.empty()
            for m in step_markers:
                m.empty()
            err_title, err_detail = get_random_error_personality()
            st.error(f"**{err_title}**  {err_detail}", icon=":material/cloud_off:")
            st.info("多次失败请联系系统管理员或在企业微信群反馈。", icon=":material/support_agent:")
            st.stop()

        # --- Finalize progress ---
        progress_bar.progress(100, text="回答完成")
        for m in step_markers:
            m.empty()
        steps_cols_final = st.columns(3)
        steps_cols_final[0].markdown(":material/check_circle: *查找政策*")
        steps_cols_final[1].markdown(":material/check_circle: *整理答案*")
        steps_cols_final[2].markdown(":material/check_circle: *核对引用*")
        # Clear progress bar after a short moment
        progress_bar.empty()
        # Clean up step placeholder columns
        progress_steps.empty()

        if completed:
            completed["citations"] = citations
            answer_slot.markdown(completed["answer"])
            render_evidence(citations)
            st.session_state.last_answer = completed
            st.session_state.chat_messages.append(
                {"role": "assistant", "result": completed}
            )

            # --- Whimsy: track answer count + achievement celebration ---
            count = track_answer_count()
            if count == 1:
                check_and_celebrate_achievement("first_answer")
            elif count == 5:
                check_and_celebrate_achievement("5_answers")
            elif count == 20:
                check_and_celebrate_achievement("20_answers")
            else:
                st.toast(get_random_success("answer"), icon="📋")

# --- Feedback ---
last = st.session_state.last_answer
if last:
    with st.container(border=True):
        st.markdown("**这次回答解决问题了吗？**")
        rating_label = st.segmented_control(
            "回答反馈",
            ["有帮助", "需要改进"],
            label_visibility="collapsed",
            key=f"rating_{last['request_id']}",
        )
        if rating_label == "需要改进":
            reasons = st.pills(
                "问题在哪里",
                ["答案不准确", "信息不完整", "引用不匹配", "响应太慢", "其他"],
                selection_mode="multi",
            )
            comment = st.text_area(
                "补充说明（可选）", max_chars=1000, placeholder="告诉我们哪里需要改进"
            )
        else:
            reasons, comment = [], None
        if rating_label and st.button("提交反馈", type="primary", icon=":material/send:"):
            reason_map = {
                "答案不准确": "incorrect_answer",
                "信息不完整": "missing_information",
                "引用不匹配": "wrong_citation",
                "响应太慢": "too_slow",
                "其他": "other",
            }
            try:
                client.create_feedback(
                    {
                        "request_id": last["request_id"],
                        "rating": "helpful" if rating_label == "有帮助" else "not_helpful",
                        "reason_codes": [reason_map[r] for r in (reasons or [])],
                        "comment": comment or None,
                    }
                )
                st.toast("感谢反馈，我们会持续改进。", icon=":material/check_circle:")
            except APIClientError as exc:
                st.error(f"反馈提交失败：{exc}")
