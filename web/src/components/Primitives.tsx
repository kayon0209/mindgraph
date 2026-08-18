import type { ReactNode } from "react";
import { AlertTriangle, Inbox, LoaderCircle } from "lucide-react";

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
  active: "在行",
  indexed: "已索引",
  confirmed: "已确认",
  rejected: "已拒绝",
  healthy: "健康",
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

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-panel error-state">
      <AlertTriangle size={24} />
      <strong>数据读取失败</strong>
      <p>{message}</p>
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
