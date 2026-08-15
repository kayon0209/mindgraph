"""
问题改进页面 — Bad Case 跟踪与处理。

UX 升级：
- 统一 Badge 系统与 Metric Card 风格
- 错误恢复替代"告知-终止"
- 空状态引导
"""
import pandas as pd
import streamlit as st

from ui.api_client import APIClientError
from ui.theme import badge, error_recovery, empty_state
from ui.whimsy import check_and_celebrate_achievement

client = st.session_state.api_client

STATUS_LABELS = {
    "new": "待分类",
    "triaged": "已分类",
    "in_progress": "处理中",
    "resolved": "已解决",
    "wont_fix": "暂不处理",
}
CATEGORY_LABELS = {
    "unclassified": "尚未分类",
    "knowledge_gap": "政策缺失",
    "chunking_error": "文档切分问题",
    "retrieval_error": "检索未命中",
    "rerank_error": "排序问题",
    "generation_error": "回答生成错误",
    "citation_error": "引用错误",
    "false_reject": "误拒答",
    "missed_reject": "应拒答未拒答",
    "provider_error": "模型服务异常",
    "system_error": "系统异常",
}
STATUS_VARIANT = {
    "new": "danger",
    "triaged": "info",
    "in_progress": "warning",
    "resolved": "success",
    "wont_fix": "muted",
}


# =========================================================================
# Page
# =========================================================================
st.title("问题改进")
st.markdown(
    "把用户负反馈转化为可跟踪的改进任务。每一次修复，小财都会变得更聪明一点。"
)

# --- Load data ---
try:
    all_cases = client.bad_cases()
except APIClientError as exc:
    error_recovery(
        title="问题列表暂时不可用",
        detail=str(exc),
        retry_action="重新加载",
    )
    st.stop()

# --- Summary metrics ---
with st.container():
    cols = st.columns(4)
    cols[0].metric("待分类", sum(item["status"] == "new" for item in all_cases), border=True)
    cols[1].metric("处理中", sum(item["status"] == "in_progress" for item in all_cases), border=True)
    cols[2].metric("已解决", sum(item["status"] == "resolved" for item in all_cases), border=True)
    cols[3].metric("问题总数", len(all_cases), border=True)

# --- Filters ---
filter_cols = st.columns(2)
with filter_cols[0]:
    status = st.selectbox(
        "处理状态",
        [None, *STATUS_LABELS],
        format_func=lambda v: STATUS_LABELS.get(v, "全部状态") if v else "全部状态",
    )
with filter_cols[1]:
    category = st.selectbox(
        "问题类型",
        [None, *CATEGORY_LABELS],
        format_func=lambda v: CATEGORY_LABELS.get(v, "全部类型") if v else "全部类型",
    )

try:
    cases = client.bad_cases(status, category)
except APIClientError as exc:
    st.error(str(exc))
    st.stop()

if not cases:
        empty_state(
            icon=":material/check_circle:",
            title="当前筛选条件下没有待处理问题",
            description="很好！要么还没发现问题，要么已经全部解决了。",
        )
else:
    rows = [
        {
            "用户问题": case.get("question") or "未记录（隐私模式）",
            "问题类型": CATEGORY_LABELS.get(case["error_category"], case["error_category"]),
            "处理状态": STATUS_LABELS.get(case["status"], case["status"]),
            "更新时间": case["updated_at"],
        }
        for case in cases
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    selected_id = st.selectbox(
        "选择问题",
        [case["bad_case_id"] for case in cases],
        format_func=lambda v: next(
            (item.get("question") or "未记录的问题")[:60]
            for item in cases
            if item["bad_case_id"] == v
        ),
    )
    case = next(item for item in cases if item["bad_case_id"] == selected_id)

    with st.container(border=True):
        st.subheader(case.get("question") or "问题未记录（隐私模式）")
        st.markdown(
            f"{badge(CATEGORY_LABELS.get(case['error_category'], case['error_category']), 'info')}  "
            f"{badge(STATUS_LABELS.get(case['status'], case['status']), STATUS_VARIANT.get(case['status'], 'muted'))}",
            unsafe_allow_html=True,
        )
        st.markdown("**系统当时的回答**")
        st.write(case.get("answer") or "没有生成回答")
        with st.expander("查看当时引用的政策", icon=":material/gavel:"):
            chunks = case.get("retrieved_chunks", [])
            if chunks:
                for chunk in chunks:
                    with st.container(border=True):
                        source = chunk.get("chunk", chunk)
                        st.markdown(f"**{source.get('document_id', '未知政策')}**")
                        st.write(source.get("text", "无文本"))
            else:
                st.caption("没有检索记录。")

    # --- Update form ---
    with st.form("bad_case_update"):
        st.subheader("处理记录")
        category_values = list(CATEGORY_LABELS)
        status_values = list(STATUS_LABELS)
        current_category = (
            case["error_category"]
            if case["error_category"] in category_values
            else "unclassified"
        )
        current_status = case["status"] if case["status"] in status_values else "new"
        form_cols = st.columns(2)
        with form_cols[0]:
            new_category = st.selectbox(
                "问题分类",
                category_values,
                index=category_values.index(current_category),
                format_func=lambda v: CATEGORY_LABELS[v],
            )
        with form_cols[1]:
            new_status = st.selectbox(
                "处理进度",
                status_values,
                index=status_values.index(current_status),
                format_func=lambda v: STATUS_LABELS[v],
            )
        note = st.text_area(
            "分析记录",
            value=case.get("reviewer_note") or "",
            placeholder="记录根因、证据和下一步",
        )
        resolution = st.text_area(
            "解决方式",
            value=case.get("resolution") or "",
            placeholder="说明修改了什么，以及如何验证",
        )
        if st.form_submit_button("保存处理记录", type="primary", icon=":material/save:"):
            try:
                client.update_bad_case(
                    selected_id,
                    {
                        "error_category": new_category,
                        "status": new_status,
                        "reviewer_note": note or None,
                        "resolution": resolution or None,
                    },
                )
                st.toast("处理记录已保存。小财的知识库又进化了一点。", icon=":material/check_circle:")
                # --- Whimsy: first bad case resolved achievement ---
                if new_status == "resolved":
                    check_and_celebrate_achievement("first_badcase_resolved")
                st.rerun()
            except APIClientError as exc:
                st.error(str(exc))

# --- Export ---
try:
    export = client.export_bad_cases(status, category)
    st.download_button(
        "导出当前列表",
        export,
        file_name="quality_issues.csv",
        mime="text/csv",
        icon=":material/download:",
    )
except APIClientError:
    pass
