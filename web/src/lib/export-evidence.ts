import { citationValidity, statusLabel } from "./citation-status";
import type { Citation, UsageInfo } from "../types";

/**
 * 证据导出（P1 研究项③）：把「问题 + 结论 + 引用依据 + 制度版本 + 时间戳」
 * 组装成可提交的 Markdown 材料，完全在客户端基于本轮证据快照生成，
 * 不新增后端接口。正式审计仍以服务端 query_logs 为准（文末注明）。
 */

export type ExportableTurn = {
  question: string;
  answer: string;
  citations?: Citation[];
  requestId?: string;
  indexVersion?: string | null;
  model?: string;
  resultState?: string | null;
  queryDate?: string;
  createdAt?: string;
  elapsedMs?: number;
  usage?: UsageInfo | null;
};

const RESULT_STATE_LABELS: Record<string, string> = {
  answered: "已回答",
  insufficient_evidence: "证据不足（拒答）",
  permission_denied: "权限不足（拒答）",
  conflicting_evidence: "版本冲突（停止生成）",
  out_of_scope: "超出范围（拒答）",
  model_unavailable: "模型不可用",
  retrieval_unavailable: "检索不可用",
  system_error: "系统错误",
  aborted: "已手动中止",
};

function formatLocalDateTime(iso?: string): string {
  const date = iso ? new Date(iso) : new Date();
  if (Number.isNaN(date.getTime())) return "未知时间";
  const pad = (value: number) => `${value}`.padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function citationBlock(citation: Citation, asOf?: string | null): string {
  const validity = citationValidity(citation, asOf);
  const meta: string[] = [];
  if (citation.policy_key) meta.push(`制度族：${citation.policy_key}`);
  meta.push(citation.document_version ? `版本：V${citation.document_version}` : "版本：未登记");
  meta.push(`状态：${statusLabel(citation.policy_status)}（${validity.label}）`);
  if (citation.effective_from || citation.effective_to) {
    meta.push(`生效区间：${citation.effective_from || "未登记"} 起${citation.effective_to ? `，至 ${citation.effective_to}` : ""}`);
  }
  if (citation.owner) meta.push(`责任人：${citation.owner}`);
  if (citation.section_path) meta.push(`章节：${citation.section_path}`);
  if (citation.vault_path) meta.push(`来源：${citation.vault_path}`);
  const lines = [
    `### [${citation.final_rank}] ${citation.document_name}`,
    ...meta.map((item) => `- ${item}`),
    "",
    "> " + citation.excerpt.replace(/\n/g, "\n> "),
  ];
  if (validity.level === "stale") {
    lines.push("", `⚠ 时效提示：${validity.detail}`);
  }
  return lines.join("\n");
}

export function buildTurnMarkdown(turn: ExportableTurn, asOf?: string | null): string {
  const citations = turn.citations ?? [];
  const sections: string[] = [
    `## 问题`,
    "",
    turn.question,
    "",
    `## 结论`,
    "",
    turn.answer || "（本次未产生完整回答）",
    "",
  ];
  if (turn.resultState && turn.resultState !== "answered") {
    sections.push(`> 结果状态：${RESULT_STATE_LABELS[turn.resultState] || turn.resultState}`, "");
  }
  sections.push(`## 引用依据（${citations.length} 条）`, "");
  if (citations.length) {
    for (const citation of citations) {
      sections.push(citationBlock(citation, asOf ?? turn.queryDate), "");
    }
  } else {
    sections.push("本次回答没有引用依据。", "");
  }
  return sections.join("\n");
}

export function buildEvidenceMarkdown(turns: ExportableTurn[], options?: { title?: string }): string {
  const first = turns[0];
  const header: string[] = [
    `# MindGraph 证据导出${options?.title ? `：${options.title}` : ""}`,
    "",
    `- 导出时间：${formatLocalDateTime()}（本机时间）`,
  ];
  if (first?.queryDate) header.push(`- 查询日期：${first.queryDate}`);
  if (first?.requestId) header.push(`- 请求 ID：${first.requestId}`);
  if (first?.indexVersion) header.push(`- 索引版本：${first.indexVersion}`);
  if (first?.model) header.push(`- 生成模型：${first.model}`);
  if (first?.elapsedMs != null) header.push(`- 总耗时：${(first.elapsedMs / 1000).toFixed(1)}s`);
  if (first?.usage?.total_tokens != null) header.push(`- Token 用量：${first.usage.total_tokens}`);
  header.push(
    "",
    "---",
    "",
  );
  const body = turns
    .map((turn, index) => (turns.length > 1 ? `# 第 ${index + 1} 轮\n\n${buildTurnMarkdown(turn)}` : buildTurnMarkdown(turn)))
    .join("\n\n---\n\n");
  const footer = [
    "",
    "---",
    "",
    "## 说明",
    "",
    "本文件由 MindGraph 客户端基于本次检索快照生成，用于工作留痕与提交佐证；",
    "引用标记 [citation-N] 对应「引用依据」中同编号条目。",
    "正式审计请以服务端查询日志（query_logs）为准。",
    "",
  ].join("\n");
  return header.join("\n") + body + footer;
}

/** 文件名：mindgraph-证据-YYYYMMDD-HHmm-问题摘要.md（去除文件系统非法字符） */
export function evidenceFilename(question: string, date = new Date()): string {
  const pad = (value: number) => `${value}`.padStart(2, "0");
  const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`;
  const slug = question.replace(/[\\/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24) || "未命名问题";
  return `mindgraph-证据-${stamp}-${slug}.md`;
}

export function downloadTextFile(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 立即释放会在部分浏览器中断下载，延迟释放更稳妥
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}
