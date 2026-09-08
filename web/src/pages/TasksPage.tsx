/**
 * TasksPage（M4-A，AGENT_TASKS_ENABLED 门控；按 docs/ui/TASKS-UI-G2-DESIGN-SPEC.md）。
 *
 * 入口：设置/侧栏区探测式（服务端 404 时不出现），不进第六主导航——
 * 默认入口由 Product Signal 决定（当前 UNVALIDATED，见 ADR-004）。
 * 轮询节奏：running 3s / queued 5s / 终态停止；失焦降频 15s。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FileDown, ListChecks, LoaderCircle, OctagonX, TriangleAlert } from "lucide-react";

import { api, ApiError } from "../lib/api";
import { downloadTextFile } from "../lib/export-evidence";
import { executeTaskAction } from "../lib/task-action-errors";
import type { AgentArtifactContent, AgentArtifactMeta, AgentTask } from "../types";
import { PageHeader } from "../components/Primitives";

type TaskView = "list" | "detail";

function statusBadge(status: AgentTask["status"]): { className: string; label: string } {
  switch (status) {
    case "queued":
      return { className: "task-badge queued", label: "已排队" };
    case "running":
      return { className: "task-badge running", label: "正在核对" };
    case "completed":
      return { className: "task-badge completed", label: "完成" };
    case "completed_with_conflicts":
      return { className: "task-badge conflict", label: "完成（有版本冲突）" };
    case "completed_empty":
      return { className: "task-badge empty", label: "无命中" };
    case "failed":
      return { className: "task-badge failed", label: "未完成" };
    case "cancelled":
      return { className: "task-badge cancelled", label: "已取消" };
  }
}

export function tasksEnabled(): Promise<boolean> {
  return api
    .listAgentTasks()
    .then(() => true)
    .catch((error: unknown) => (error instanceof ApiError ? error.status !== 404 : false));
}

export function TasksPage() {
  const [view, setView] = useState<TaskView>("list");
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [selected, setSelected] = useState<AgentTask | null>(null);
  const [selectedArtifacts, setSelectedArtifacts] = useState<AgentArtifactMeta[]>([]);
  const [artifactPreview, setArtifactPreview] = useState<AgentArtifactContent | null>(null);
  const [pollError, setPollError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const refreshList = useCallback(async (): Promise<void> => {
    try {
      const page = await api.listAgentTasks();
      setTasks(page.items);
      setPollError(false);
    } catch {
      setPollError(true);
    }
  }, []);

  // 轮询：有活跃任务才继续；页面失焦降频
  useEffect(() => {
    let cancelled = false;
    const schedule = () => {
      const hasActive = tasks.some((task) => task.status === "queued" || task.status === "running");
      const interval = pollError ? 10000 : document.hidden ? 15000 : hasActive ? 3000 : 0;
      if (interval === 0) {
        pollTimer.current = window.setTimeout(schedule, 15000); // 终态低频兜底刷新
        return;
      }
      pollTimer.current = window.setTimeout(async () => {
        if (!cancelled) {
          await refreshList();
          schedule();
        }
      }, interval);
    };
    schedule();
    return () => {
      cancelled = true;
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [tasks, pollError, refreshList]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const openDetail = async (task: AgentTask) => {
    setActionError(null);
    const outcome = await executeTaskAction("detail", () => api.getAgentTask(task.task_id));
    if (!outcome.ok) {
      setActionError(outcome.message);
      return;
    }
    const detail = outcome.value;
    setSelected(detail);
    setSelectedArtifacts(detail.artifacts ?? []);
    setArtifactPreview(null);
    setView("detail");
  };

  const submitTask = async () => {
    if (!query.trim() || submitting) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const outcome = await executeTaskAction("submit", () => api.submitAgentTask(
        { document_query: query.trim(), top_k: 10 },
        `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
      ));
      if (!outcome.ok) {
        setActionError(outcome.message);
        return;
      }
      setQuery("");
      await refreshList();
    } finally {
      setSubmitting(false);
    }
  };

  const cancelTask = async (task: AgentTask) => {
    setConfirmingCancel(task.task_id);
  };

  const confirmCancel = async (task: AgentTask) => {
    setConfirmingCancel(null);
    setActionError(null);
    const outcome = await executeTaskAction("cancel", () => api.cancelAgentTask(task.task_id));
    if (!outcome.ok) {
      setActionError(outcome.message);
      return;
    }
    await refreshList();
  };

  const loadArtifact = async (meta: AgentArtifactMeta) => {
    if (!selected) return;
    setActionError(null);
    const outcome = await executeTaskAction("artifact", () => api.getAgentArtifact(selected.task_id, meta.artifact_id));
    if (!outcome.ok) {
      setActionError(outcome.message);
      return;
    }
    setArtifactPreview(outcome.value);
  };

  const exportArtifact = () => {
    if (!artifactPreview) return;
    const lines = [
      `# 核对证据包 · ${artifactPreview.title}`,
      ``,
      `检索词：${artifactPreview.content.document_query ?? "—"}`,
      `判定日期：${artifactPreview.content.as_of ?? "—"}`,
      `命中文档：${artifactPreview.content.matched_documents ?? 0}`,
      `版本冲突：${artifactPreview.content.conflict_count ?? 0}`,
      `校验和：${artifactPreview.checksum}`,
      ``,
      ...artifactPreview.evidence_snapshot.map(
        (item, index) =>
          `[${index + 1}] ${item.document_name} · ${item.document_version ?? "版本未登记"} · ` +
          `${item.policy_status ?? "状态未登记"} · 生效 ${item.effective_from ?? "—"}` +
          (item.excerpt ? `\n    摘要：${item.excerpt.slice(0, 120)}` : ""),
      ),
      ...(artifactPreview.content.conflicts?.length
        ? ["", "## 版本冲突（需制度责任人裁决）", ...artifactPreview.content.conflicts.map((c) => `- ${c.policy_key}：${(c.versions ?? []).length} 个有效版本`)]
        : []),
    ];
    downloadTextFile(`mindgraph-任务证据包-${artifactPreview.artifact_id.slice(4, 12)}.md`, lines.join("\n"));
  };

  return (
    <section className="page tasks-page">
      <PageHeader eyebrow="后台核对任务" title="任务" description="提交核对任务后在后台运行；完成后可导出仅你可见的证据包。" meta={["不影响当前对话", "证据包为私有存档"]} />
      {pollError ? (
        <p className="task-poll-error" role="status">连接中断，正在重试获取最新状态…</p>
      ) : null}
      {actionError ? (
        <p className="task-action-error" role="alert"><TriangleAlert size={14} /> {actionError}</p>
      ) : null}

      {view === "list" ? (
        <>
          <form
            className="task-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void submitTask();
            }}
          >
            <label htmlFor="task-query">要核对什么制度？</label>
            <input
              id="task-query"
              maxLength={500}
              onChange={(event) => setQuery(event.target.value)}
              placeholder='例如：核对"差旅报销"相关制度在今天的有效版本'
              value={query}
            />
            <button className="button primary" disabled={!query.trim() || submitting} type="submit">
              {submitting ? "已提交，排队中" : "开始后台核对"}
            </button>
          </form>

          {tasks.length === 0 ? (
            <p className="task-empty">
              没有后台任务。试试：核对"差旅报销"相关制度在今天的有效版本。
            </p>
          ) : (
            <ul className="task-list" aria-live="polite">
              {tasks.map((task) => {
                const badge = statusBadge(task.status);
                const summary = String(task.constraints.document_query ?? "（无检索词）");
                return (
                  <li className="task-card" key={task.task_id}>
                    <div className="task-card-head">
                      <span className={badge.className}>{badge.label}</span>
                      <strong className="task-summary" title={summary}>{summary}</strong>
                      <small>{new Date(task.updated_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</small>
                    </div>
                    <div className="task-card-actions">
                      <button className="button secondary small" onClick={() => void openDetail(task)} type="button">
                        <ListChecks size={13} /> 详情
                      </button>
                      {task.status === "queued" || task.status === "running" ? (
                        confirmingCancel === task.task_id ? (
                          <button className="button danger small" onClick={() => void confirmCancel(task)} type="button" autoFocus>
                            <OctagonX size={13} /> 确认取消
                          </button>
                        ) : (
                          <button className="button secondary small" onClick={() => void cancelTask(task)} type="button">
                            <OctagonX size={13} /> 取消
                          </button>
                        )
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      ) : (
        <div className="task-detail">
          {selected ? (
            <>
              <button className="button secondary small" onClick={() => setView("list")} type="button">← 返回任务列表</button>
              <div className="task-detail-head">
                <span className={statusBadge(selected.status).className}>{statusBadge(selected.status).label}</span>
                <strong>{String(selected.constraints.document_query ?? "（无检索词）")}</strong>
              </div>
              <dl className="task-detail-meta">
                <div><dt>状态</dt><dd>{statusBadge(selected.status).label}</dd></div>
                <div><dt>尝试次数</dt><dd>{selected.attempt_count}</dd></div>
                <div><dt>提交时间</dt><dd>{new Date(selected.created_at).toLocaleString("zh-CN")}</dd></div>
                {selected.error_message ? <div><dt>失败原因</dt><dd>{selected.error_message}</dd></div> : null}
              </dl>
              {selected.status === "completed_with_conflicts" ? (
                <p className="task-conflict-note" role="alert">
                  <TriangleAlert size={14} /> 部分文档存在多个同时生效版本，已停止给出结论。请在证据包中查看冲突版本族，由制度责任人裁决。
                </p>
              ) : null}
              {selected.status === "completed" && selectedArtifacts.length ? (
                <p className="task-ok-note"><CheckCircle2 size={14} /> 核对完成，证据包已生成为私有存档。</p>
              ) : null}

              {selectedArtifacts.length ? (
                <section className="task-artifacts">
                  <h3>证据包</h3>
                  {selectedArtifacts.map((meta) => (
                    <div className="task-artifact-row" key={meta.artifact_id}>
                      <span>{meta.title}</span>
                      <button className="button secondary small" onClick={() => void loadArtifact(meta)} type="button">
                        查看
                      </button>
                    </div>
                  ))}
                  {artifactPreview ? (
                    <>
                      <div className="task-artifact-preview">
                        <p>
                          命中 {artifactPreview.content.matched_documents ?? 0} 篇 ·
                          冲突 {artifactPreview.content.conflict_count ?? 0} 处 ·
                          校验和 {artifactPreview.checksum.slice(0, 12)}…
                        </p>
                        <ol>
                          {artifactPreview.evidence_snapshot.map((item, index) => (
                            <li key={`${item.citation_id}-${index}`}>
                              <strong>{item.document_name}</strong>{" "}
                              <small>{item.document_version ?? "版本未登记"} · {item.policy_status ?? "状态未登记"} · 生效 {item.effective_from ?? "—"}</small>
                            </li>
                          ))}
                        </ol>
                      </div>
                      <button className="button secondary" onClick={exportArtifact} type="button">
                        <FileDown size={14} /> 导出证据包
                      </button>
                    </>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : (
            <p><LoaderCircle size={14} className="spin" /> 正在加载任务详情…</p>
          )}
        </div>
      )}
    </section>
  );
}
