"""
政策中心页面 — 文档查阅与版本管理。

UX 升级：
- "一键发布"：上传 + 解析 + 发布合并为一个流程（消除 8 步割裂）
- 发布进度实时可视化（解析 → 索引构建 → 验证 → 激活）
- 错误恢复替代"告知-终止"，含重试入口
- 状态徽章统一视觉语言
"""
import pandas as pd
import streamlit as st

from ui.api_client import APIClientError
from ui.theme import badge, error_recovery, empty_state, loading_progress, card
from ui.whimsy import (
    check_and_celebrate_achievement,
    get_random_error_personality,
    get_random_success,
    PUBLISH_LOADING,
)

client = st.session_state.api_client

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
STATUS_LABELS = {
    "draft": "草稿",
    "pending_index": "待发布",
    "active": "已发布",
    "expired": "已失效",
    "replaced": "已被替代",
    "deleted": "已停用",
    "parse_failed": "解析失败",
    "index_failed": "发布失败",
}
AUTHORITY_LABELS = {
    "official_policy": "正式制度",
    "official_guideline": "官方指引",
    "approved_faq": "已审核问答",
    "user_uploaded_reference": "补充资料",
    "external_reference": "外部参考",
}
TRANSITIONS = {
    "draft": ["pending_index", "deleted"],
    "pending_index": ["active", "index_failed", "deleted"],
    "active": ["replaced", "expired", "deleted"],
    "index_failed": ["pending_index", "deleted"],
    "parse_failed": ["draft", "deleted"],
}
ACTION_LABELS = {
    "pending_index": "提交发布",
    "active": "确认发布",
    "index_failed": "标记发布失败",
    "replaced": "标记为已替代",
    "expired": "标记为已失效",
    "deleted": "停用此版本",
    "draft": "退回草稿",
}
STATUS_VARIANT = {
    "active": "success",
    "pending_index": "info",
    "draft": "muted",
    "expired": "warning",
    "replaced": "warning",
    "deleted": "danger",
    "parse_failed": "danger",
    "index_failed": "danger",
}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("政策中心")
st.markdown(
    "查阅当前有效政策，或由政策管理员维护新版本。所有历史版本都会保留，发布失败不会影响现行政策。"
)

# --- Load data ---
try:
    status = client.index_status()
    documents = client.document_versions()
except APIClientError as exc:
    error_recovery(
        title="政策库暂时不可用",
        detail=str(exc),
        retry_action="重新连接",
    )
    st.stop()

# --- Summary metrics ---
active_count = sum(item["status"] == "active" for item in documents)
pending_count = sum(
    item["status"] in {"draft", "pending_index", "index_failed"} for item in documents
)
with st.container():
    cols = st.columns(4)
    cols[0].metric("现行政策", active_count, border=True)
    cols[1].metric("待处理版本", pending_count, border=True)
    cols[2].metric("政策片段", status.get("chunk_count", 0), border=True)
    cols[3].metric(
        "服务状态",
        "待更新" if status.get("pending_changes") else "已同步",
        border=True,
    )

# --- Tabs ---
browse_tab, manage_tab = st.tabs(["查阅政策", "版本管理"])

# =========================================================================
# Tab 1: Browse
# =========================================================================
with browse_tab:
    categories = sorted({item["knowledge_category"] for item in documents})
    filter_cols = st.columns([3, 2, 2])
    with filter_cols[0]:
        search = st.text_input(
            "搜索政策", placeholder="输入政策名称或类别", icon=":material/search:", label_visibility="collapsed"
        )
    with filter_cols[1]:
        category = st.selectbox(
            "政策类别",
            [None, *categories],
            format_func=lambda v: v or "全部类别",
            label_visibility="collapsed",
        )
    with filter_cols[2]:
        include_history = st.toggle("显示历史版本")

    visible = [
        item for item in documents if (include_history or item["status"] == "active")
    ]
    if category:
        visible = [item for item in visible if item["knowledge_category"] == category]
    if search:
        kw = search.lower()
        visible = [
            item
            for item in visible
            if kw in item["title"].lower() or kw in item["knowledge_category"].lower()
        ]

    if not visible:
        empty_state(
            icon=":material/search_off:",
            title="没有找到符合条件的政策",
            description="尝试调整筛选条件或清除搜索关键词。",
        )
    else:
        rows = [
            {
                "政策名称": item["title"],
                "版本": item["version"],
                "类别": item["knowledge_category"],
                "效力": AUTHORITY_LABELS.get(item["authority_level"], item["authority_level"]),
                "状态": STATUS_LABELS.get(item["status"], item["status"]),
                "生效日期": item.get("effective_date") or "未标注",
                "失效日期": item.get("expiration_date") or "—",
            }
            for item in visible
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        selected_label = st.selectbox(
            "查看政策详情",
            [f"{item['title']} · {item['version']}" for item in visible],
        )
        selected = visible[
            [f"{item['title']} · {item['version']}" for item in visible].index(selected_label)
        ]
        with st.container(border=True):
            st.subheader(selected["title"])
            st.markdown(
                f"{badge(STATUS_LABELS.get(selected['status'], selected['status']), STATUS_VARIANT.get(selected['status'], 'muted'))}  "
                f"{badge(AUTHORITY_LABELS.get(selected['authority_level'], selected['authority_level']), 'brand')}",
                unsafe_allow_html=True,
            )
            st.caption(
                f"版本 {selected['version']} · {selected['knowledge_category']} · 文件格式 {selected['file_type'].upper()}"
            )
            diagnostics = selected.get("parsing_diagnostics", {})
            st.write(
                f"已提取 {diagnostics.get('elements', 0)} 个内容元素，生成 {diagnostics.get('chunks', 0)} 个可检索片段。"
            )
            if diagnostics.get("warnings"):
                st.warning("该版本存在解析提示，请管理员复核。", icon=":material/warning:")

# =========================================================================
# Tab 2: Manage (One-Click Publish)
# =========================================================================
with manage_tab:
    st.subheader("上传新版本")
    st.caption("上传后系统自动解析并构建索引，发布失败不会影响现行政策。")

    with st.form("version_upload"):
        uploaded = st.file_uploader(
            "选择政策文件",
            type=["md", "txt", "pdf", "docx", "xlsx"],
            accept_multiple_files=False,
        )
        form_cols = st.columns(3)
        with form_cols[0]:
            version = st.text_input("版本号", value="v1", placeholder="例如 2026.1")
        with form_cols[1]:
            category_value = st.text_input("政策类别", value="other", placeholder="例如 差旅费")
        with form_cols[2]:
            logical_id = st.text_input(
                "关联已有政策 ID（新政策留空）",
                placeholder="新政策无需填写",
                help="上传现有政策的新版本时填写；新政策无需填写。",
            )
        authority = st.selectbox(
            "文件效力",
            list(AUTHORITY_LABELS),
            format_func=lambda v: AUTHORITY_LABELS[v],
        )
        # --- New: publish-now toggle ---
        publish_now = st.toggle(
            "上传后立即发布",
            value=True,
            help="关闭时仅保存为草稿，后续手动发布。",
        )
        upload_submit = st.form_submit_button(
            "保存并发布" if publish_now else "保存为草稿",
            type="primary",
            icon=":material/publish:" if publish_now else ":material/upload_file:",
        )

    if upload_submit:
        if not uploaded:
            st.warning("请先选择政策文件。")
        else:
            # --- One-click flow: upload → parse → (optional) publish ---
            publish_progress = st.empty()
            with publish_progress.container():
                progress_bar = st.progress(0, text=PUBLISH_LOADING[0])

            try:
                # Step 1: upload + parse
                result = client.upload_document_version(
                    uploaded.name,
                    uploaded.getvalue(),
                    {
                        "logical_document_id": logical_id,
                        "version": version,
                        "category": category_value,
                        "authority_level": authority,
                    },
                )
                diagnostics = result["parsing_diagnostics"]
                if diagnostics.get("status") == "failed":
                    publish_progress.empty()
                    st.error(
                        "文件已保存，但解析失败。请检查文件格式后重新上传。",
                        icon=":material/error:",
                    )
                    if "ocr" in str(diagnostics.get("warnings", "")).lower():
                        st.info(
                            "检测到可能是扫描件 PDF。请上传可选中文字的原生 PDF 或 Markdown 文件。",
                            icon=":material/lightbulb:",
                        )
                else:
                    progress_bar.progress(
                        50, text=f"解析完成：{diagnostics.get('elements', 0)} 个元素，{diagnostics.get('chunks', 0)} 个片段"
                    )

                    if publish_now:
                        # Step 2: submit for indexing (whimsy copy)
                        progress_bar.progress(60, text=PUBLISH_LOADING[2])

                        # Step 3: incremental rebuild
                        progress_bar.progress(70, text=PUBLISH_LOADING[3])
                        index_result = client.incremental_rebuild()

                        progress_bar.progress(100, text=PUBLISH_LOADING[4])
                        publish_progress.empty()

                        # --- Whimsy: publish celebration + achievement ---
                        st.success(
                            f"新政策已生效！索引版本：{index_result['index_version']}",
                            icon=":material/check_circle:",
                        )
                        check_and_celebrate_achievement("first_publish")
                        st.toast(get_random_success("publish"), icon="📚")
                        st.balloons()
                        st.rerun()
                    else:
                        progress_bar.progress(100, text="解析完成，已保存为草稿")
                        publish_progress.empty()
                        st.success(
                            f"解析完成：{diagnostics.get('elements', 0)} 个内容元素，{diagnostics.get('chunks', 0)} 个政策片段。已保存为草稿。",
                            icon=":material/check_circle:",
                        )
                        st.rerun()

            except APIClientError as exc:
                publish_progress.empty()
                err_title, err_detail = get_random_error_personality()
                st.error(f"**{err_title}**  {err_detail}", icon=":material/error:")
                st.caption(f"技术详情：{exc}")
                if st.button("🔄 让小财再试一次", key="retry_upload"):
                    st.rerun()

    # --- Manual version management ---
    st.subheader("待处理版本")
    manageable = [item for item in documents if item["status"] in TRANSITIONS]
    if not manageable:
        empty_state(
            icon=":material/check_circle:",
            title="当前没有待处理版本",
            description="上传新政策文件后将出现在这里。",
        )
    else:
        selected_label = st.selectbox(
            "选择版本",
            [
                f"{item['title']} · {item['version']} · {STATUS_LABELS[item['status']]}"
                for item in manageable
            ],
        )
        selected = manageable[
            [
                f"{item['title']} · {item['version']} · {STATUS_LABELS[item['status']]}"
                for item in manageable
            ].index(selected_label)
        ]
        with st.container(border=True):
            st.markdown(f"**{selected['title']} · {selected['version']}**")
            st.caption(
                f"当前状态：{STATUS_LABELS[selected['status']]} · {selected['knowledge_category']}"
            )
            diagnostics = selected.get("parsing_diagnostics", {})
            st.write(
                f"解析结果：{diagnostics.get('elements', 0)} 个元素，{diagnostics.get('chunks', 0)} 个片段"
            )
            next_state = st.selectbox(
                "下一步",
                TRANSITIONS[selected["status"]],
                format_func=lambda v: ACTION_LABELS[v],
            )
            if st.button(ACTION_LABELS[next_state], type="primary", icon=":material/arrow_forward:"):
                try:
                    client.transition_document(selected["document_id"], next_state)
                    st.toast("版本状态已更新。", icon=":material/check_circle:")
                    st.rerun()
                except APIClientError as exc:
                    st.error(str(exc))

    # --- Index maintenance ---
    with st.expander("索引维护", icon=":material/settings_backup_restore:"):
        st.caption("仅在政策版本变更后需要。系统会先验证新索引，成功后才切换。")
        col_build, col_rollback = st.columns(2)
        with col_build:
            if st.button("发布所有待更新政策", type="primary", icon=":material/publish:", use_container_width=True):
                with st.status("正在构建并验证政策索引…", expanded=True) as build_status:
                    try:
                        result = client.incremental_rebuild()
                        build_status.update(label="新政策索引已发布", state="complete")
                        st.caption(
                            f"版本：{result['index_version']} · 复用 Embedding：{result.get('reused_embeddings', 0)}"
                        )
                    except APIClientError as exc:
                        build_status.update(label=str(exc), state="error")
        with col_rollback:
            if st.button("回滚到上一个版本", icon=":material/undo:", use_container_width=True):
                try:
                    client.rollback_index("Policy center operator rollback")
                    st.toast("已恢复上一个政策索引。", icon=":material/check_circle:")
                    st.rerun()
                except APIClientError as exc:
                    st.error(str(exc))

        try:
            versions = client.index_versions()
            if versions:
                index_rows = [
                    {
                        "索引版本": item["index_version"],
                        "状态": item["status"],
                        "创建时间": item["created_at"],
                        "激活时间": item.get("activated_at") or "—",
                    }
                    for item in versions
                ]
                st.dataframe(pd.DataFrame(index_rows), hide_index=True, use_container_width=True)
        except APIClientError as exc:
            st.error(str(exc))
