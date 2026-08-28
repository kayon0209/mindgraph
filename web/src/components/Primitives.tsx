import { useState, type ReactNode } from "react";
import { AlertTriangle, Inbox, LoaderCircle, X } from "lucide-react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header reveal reveal-1">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  active: "生效中",
  indexed: "已索引",
  confirmed: "已确认",
  rejected: "已拒绝",
  healthy: "健康",
  proposed: "待审核",
  unknown: "未知",
};

export function StatusPill({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const tone = ["active", "completed", "indexed", "confirmed", "healthy"].includes(normalized)
    ? "positive"
    : ["failed", "rejected", "conflict"].includes(normalized)
      ? "negative"
      : "neutral";
  return <span className={`status-pill ${tone}`}>{STATUS_LABELS[normalized] || value || "unknown"}</span>;
}

export function LoadingState({ label = "正在读取真实数据" }: { label?: string }) {
  return (
    <div className="state-panel">
      <LoaderCircle className="spin" size={22} />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="state-panel empty-state">
      <Inbox size={24} />
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

const ERROR_MESSAGES: Record<string, string> = {
  retrieval_unavailable: "检索服务暂不可用，请稍后重试。",
  provider_error: "生成模型暂时不可用，可先查看引用原文。",
  stream_error: "回答连接中断，请重试。",
};

export function ErrorState({ message, detail, onRetry }: { message: string; detail?: string; onRetry?: () => void }) {
  const friendlyMessage = ERROR_MESSAGES[message] || message;
  return (
    <div className="state-panel error-state" role="alert">
      <AlertTriangle size={24} />
      <strong>暂时无法完成</strong>
      <p>{friendlyMessage}</p>
      {detail ? (
        <details className="technical-details">
          <summary>查看技术详情</summary>
          <code>{detail}</code>
        </details>
      ) : null}
      {onRetry ? (
        <button className="button secondary" onClick={onRetry} type="button">
          重试
        </button>
      ) : null}
    </div>
  );
}

export function MetricCard({ value, label, note }: { value: string | number; label: string; note?: string }) {
  return (
    <article className="metric-card">
      <span className="metric-value">{value}</span>
      <span className="metric-label">{label}</span>
      {note ? <span className="metric-note">{note}</span> : null}
    </article>
  );
}

/**
 * I1：每个视图首次进入时给一行上下文解释，可关闭且关闭状态持久化。
 * storageKey 各视图唯一，如 "mindgraph.hint.relations"。
 */
export function ContextHint({ storageKey, children }: { storageKey: string; children: ReactNode }) {
  const [visible, setVisible] = useState(() => {
    try {
      return !window.localStorage.getItem(storageKey);
    } catch {
      return true;
    }
  });
  if (!visible) return null;
  const dismiss = () => {
    setVisible(false);
    try {
      window.localStorage.setItem(storageKey, "1");
    } catch {
      // 存储失败可忽略：下次进入仍显示
    }
  };
  return (
    <div className="context-hint" role="note">
      <p>{children}</p>
      <button className="context-hint-dismiss" onClick={dismiss} type="button" aria-label="关闭提示">
        <X size={14} />
      </button>
    </div>
  );
}
