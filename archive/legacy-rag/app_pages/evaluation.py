"""
质量看板页面 — 检索评测与人工复核。

UX 升级：
- 三重 API 调用从串行改为并行加载（消除累积等待）
- 评测创建后增量更新而非全页 `st.rerun()` 闪白
- 错误恢复、空状态、骨架屏统一
- 视觉一致性：统一 metric card / badge / progress column
"""
import pandas as pd
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from ui.api_client import APIClientError
from ui.theme import badge, error_recovery, empty_state, skeleton_block
from ui.whimsy import (
    check_and_celebrate_achievement,
    get_random_error_personality,
    EVAL_LOADING,
)

client = st.session_state.api_client

STRATEGY_LABELS = {
    "dense": "语义检索",
    "bm25": "关键词检索",
    "hybrid": "混合检索",
    "hybrid_rerank": "混合检索 + 精排",
}
STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "interrupted": "服务重启中断",
}


def dataset_label(dataset_id: str, datasets: list[dict]) -> str:
    item = next((d for d in datasets if d["dataset_id"] == dataset_id), None)
    if not item:
        return dataset_id
    purpose = {
        "development": "开发验证集",
        "regression": "回归保护集",
        "holdout": "独立验收集",
        "adversarial": "对抗测试集",
    }.get(item["dataset_type"], item["dataset_type"])
    return f"{purpose} · {item['case_count']} 题"


# =========================================================================
# Page
# =========================================================================
st.title("质量看板")
st.markdown(
    "持续观察政策命中、引用完整性和失败样本。指标用于版本比较，不代表生产环境效果。"
)

# --- Parallel API loading ---
config = datasets = runs = None
load_error = None

config_placeholder = st.empty()
skeleton_block(lines=2, heading=True)

try:
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(client.public_config): "config",
            pool.submit(client.datasets): "datasets",
            pool.submit(client.evaluation_runs): "runs",
        }
        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except APIClientError as exc:
                load_error = exc
                break
        config = results.get("config")
        datasets = results.get("datasets", [])
        runs = results.get("runs", [])
except APIClientError as exc:
    load_error = exc

config_placeholder.empty()

if load_error:
    error_recovery(
        title="质量数据暂时不可用",
        detail=str(load_error),
        retry_action="重新加载",
    )
    st.stop()

# --- Summary metrics ---
completed_runs = [run for run in runs if run["status"] == "completed"]
failed_count = sum(len(run.get("failed_cases", [])) for run in completed_runs)
holdout = next((d for d in datasets if d["dataset_type"] == "holdout"), None)

with st.container():
    cols = st.columns(4)
    cols[0].metric("评测运行", len(runs), border=True)
    cols[1].metric("已完成", len(completed_runs), border=True)
    cols[2].metric("待分析样本", failed_count, border=True)
    cols[3].metric(
        "独立 Holdout",
        "未就绪" if not holdout or holdout["case_count"] == 0 else str(holdout["case_count"]),
        border=True,
    )

# --- Tabs ---
overview_tab, review_tab, dataset_tab = st.tabs(["版本表现", "人工复核", "数据集"])

# =========================================================================
# Tab 1: Version Overview
# =========================================================================
with overview_tab:
    if not runs:
        empty_state(
            icon=":material/monitoring:",
            title="还没有评测记录",
            description="运行第一次评测，看看小财检索政策有多准。",
        )
    else:
        selected_id = st.selectbox(
            "选择评测版本",
            [run["run_id"] for run in runs],
            format_func=lambda v: next(
                f"{STATUS_LABELS.get(item['status'], item['status'])} · {item['dataset_name']} · {item.get('started_at') or '未开始'}"
                for item in runs
                if item["run_id"] == v
            ),
        )
        run = next(item for item in runs if item["run_id"] == selected_id)
        st.markdown(
            badge(STATUS_LABELS.get(run["status"], run["status"]), "info"),
            unsafe_allow_html=True,
        )
        st.caption(
            f"数据集 {run['dataset_name']} · 索引 {run.get('index_version') or '未记录'} · Prompt {run.get('prompt_version') or '未记录'}"
        )
        if run["summary_metrics"]:
            strategies = list(run["summary_metrics"])
            selected_strategy = st.segmented_control(
                "检索方案",
                strategies,
                default=strategies[0],
                format_func=lambda v: STRATEGY_LABELS.get(v, v),
            )
            metrics = run["summary_metrics"][selected_strategy]
            with st.container():
                metric_cols = st.columns(4)
                metric_cols[0].metric("前 5 条证据召回", f"{metrics.get('recall_at_5', 0):.1%}", border=True)
                metric_cols[1].metric("首条命中", f"{metrics.get('recall_at_1', 0):.1%}", border=True)
                metric_cols[2].metric("文档命中", f"{metrics.get('document_hit_rate', 0):.1%}", border=True)
                metric_cols[3].metric("平均耗时", f"{metrics.get('mean_retrieval_latency_ms', 0):.0f} ms", border=True)
            metric_rows = [
                {
                    "检索方案": STRATEGY_LABELS.get(name, name),
                    "Recall@1": values.get("recall_at_1"),
                    "Recall@3": values.get("recall_at_3"),
                    "Recall@5": values.get("recall_at_5"),
                    "MRR": values.get("mrr"),
                    "平均耗时(ms)": values.get("mean_retrieval_latency_ms"),
                }
                for name, values in run["summary_metrics"].items()
            ]
            st.dataframe(
                pd.DataFrame(metric_rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Recall@1": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
                    "Recall@3": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
                    "Recall@5": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
                    "MRR": st.column_config.NumberColumn(format="%.3f"),
                },
            )
        elif run["status"] in {"queued", "running"}:
            st.info("评测正在后台运行。服务重启会中断当前任务。", icon=":material/hourglass_top:")
        else:
            st.error(run.get("error") or "评测未产生结果。", icon=":material/error:")

        if run.get("failed_cases"):
            with st.expander(f"查看失败样本（{len(run['failed_cases'])}）", icon=":material/troubleshoot:"):
                st.dataframe(pd.DataFrame(run["failed_cases"]), hide_index=True, use_container_width=True)

    # --- New evaluation ---
    with st.expander("运行新评测", icon=":material/play_circle:"):
        st.markdown(
            "**它会做什么？** 用同一批已标注问题分别测试所选检索方案，比较政策证据命中率与响应耗时。它不会训练模型，也不会修改知识库。"
        )
        st.caption(
            "开发验证集用于发现问题；回归保护集用于确认新版本没有退步。"
        )
        with st.form("evaluation_form"):
            dataset_options = [
                d["dataset_id"] for d in datasets if d["dataset_type"] != "holdout"
            ] or ["expense_qa_v1"]
            dataset = st.selectbox(
                "选择问题集",
                dataset_options,
                format_func=lambda v: dataset_label(v, datasets),
                help="开发验证集用于日常调试；回归保护集应在准备发布新版本时运行。",
            )
            strategies = st.pills(
                "要比较的检索方案",
                config.get("retrieval_strategies", []),
                default=["hybrid", "hybrid_rerank"],
                selection_mode="multi",
                format_func=lambda v: STRATEGY_LABELS.get(v, v),
            )
            run_scale = st.segmented_control(
                "运行规模",
                ["快速检查", "稳定测量"],
                default="快速检查",
                help="快速检查每题运行 1 次；稳定测量每题运行 3 次。",
            )
            repetitions = 1 if run_scale == "快速检查" else 3
            estimated_queries = (
                next(
                    (d["case_count"] for d in datasets if d["dataset_id"] == dataset), 0
                )
                * len(strategies)
                * repetitions
            )
            st.caption(f"预计执行约 {estimated_queries} 次检索；只评测检索，不调用生成模型。")
            submitted = st.form_submit_button("开始评测", type="primary", icon=":material/play_arrow:")

        if submitted:
            if not strategies:
                st.warning("至少选择一个检索方案。")
            else:
                try:
                    new_run = client.create_evaluation(
                        {
                            "dataset_name": dataset,
                            "retrieval_strategies": strategies,
                            "repetitions": repetitions,
                            "warmups": 1,
                            "evaluate_generation": False,
                        }
                    )
                    st.toast(f"评测已启动！运行 ID：{new_run['run_id'][:8]}", icon=":material/check_circle:")
                    # --- Whimsy: first eval achievement ---
                    check_and_celebrate_achievement("first_eval")
                    # Inline update instead of full-page flash
                    st.info(
                        "评测已在后台运行。稍后回来查看结果——小财正在努力刷题中。",
                        icon=":material/hourglass_top:",
                    )
                except APIClientError as exc:
                    st.error(str(exc))

# =========================================================================
# Tab 2: Human Review
# =========================================================================
with review_tab:
    if not runs:
        empty_state(
            icon=":material/rate_review:",
            title="先运行评测，再进行人工复核",
            description="评测结果出来后可在此逐题打分。",
        )
    else:
        selected_run = st.selectbox(
            "评测记录", [run["run_id"] for run in runs], key="review_run"
        )
        st.caption("人工评分与自动 Judge 分开保存；单一评审结果不会被包装成客观结论。")
        dimensions = {
            "correctness": "答案正确",
            "completeness": "信息完整",
            "citation_support": "引用支持",
            "evidence_consistency": "证据一致",
            "actionability": "可执行性",
            "refusal_appropriateness": "拒答恰当",
        }
        with st.form("human_review"):
            form_cols = st.columns(2)
            with form_cols[0]:
                case_id = st.text_input("样本 ID")
            with form_cols[1]:
                reviewer = st.text_input("评审人")
            scores = {
                name: st.slider(label, 0.0, 1.0, 1.0, 0.1)
                for name, label in dimensions.items()
            }
            reason = st.text_area("评分依据", placeholder="说明关键证据和扣分原因")
            review_submit = st.form_submit_button("保存复核", type="primary", icon=":material/save:")

        if review_submit:
            if not case_id or not reviewer:
                st.warning("请填写样本 ID 和评审人。")
            else:
                try:
                    review = client.create_human_review(
                        {
                            "run_id": selected_run,
                            "case_id": case_id,
                            "reviewer": reviewer,
                            "scores": scores,
                            "reason": reason or None,
                        }
                    )
                    msg = (
                        "复核已保存；当前仍是单评审结果。"
                        if review["single_reviewer"]
                        else "复核已保存。"
                    )
                    st.toast(msg, icon=":material/check_circle:")
                except APIClientError as exc:
                    st.error(str(exc))

# =========================================================================
# Tab 3: Datasets
# =========================================================================
with dataset_tab:
    if not datasets:
        empty_state(
            icon=":material/dataset:",
            title="暂无可用的数据集",
            description="请确保评测数据集已正确配置。",
        )
    else:
        rows = [
            {
                "数据集": d["dataset_id"],
                "版本": d["version"],
                "用途": {
                    "development": "开发调优",
                    "regression": "版本回归",
                    "holdout": "独立验收",
                    "adversarial": "对抗测试",
                }.get(d["dataset_type"], d["dataset_type"]),
                "样本数": d["case_count"],
                "标注状态": d["annotation_status"],
                "说明": d["purpose"],
            }
            for d in datasets
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if holdout and holdout["case_count"] == 0:
            st.warning(
                "独立 Holdout 仍为空。当前指标只能用于开发和回归比较，不能证明生产效果。",
                icon=":material/warning:",
            )
