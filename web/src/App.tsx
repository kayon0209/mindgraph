import { useEffect, useState } from "react";
import {
  Activity,
  BookOpenText,
  GitPullRequestArrow,
  MessageSquareText,
  Network,
  ShieldCheck,
} from "lucide-react";

import { api } from "./lib/api";
import { ChatPage } from "./pages/ChatPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { RelationsPage } from "./pages/RelationsPage";
import type { ViewId } from "./types";

const NAV_ITEMS = [
  { id: "chat" as const, label: "可信问答", index: "01", icon: MessageSquareText },
  { id: "knowledge" as const, label: "制度台账", index: "02", icon: BookOpenText },
  { id: "evaluation" as const, label: "质量账本", index: "03", icon: Activity },
  { id: "relations" as const, label: "关系审核", index: "04", icon: GitPullRequestArrow },
];

export function App() {
  const [view, setView] = useState<ViewId>("chat");
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    api
      .health()
      .then(() => active && setOnline(true))
      .catch(() => active && setOnline(false));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            <Network size={21} strokeWidth={1.7} />
          </span>
          <div>
            <strong>MindGraph</strong>
            <span>依据工作台</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={view === item.id ? "nav-button active" : "nav-button"}
                key={item.id}
                onClick={() => setView(item.id)}
                type="button"
              >
                <span className="nav-index">{item.index}</span>
                <Icon size={18} strokeWidth={1.8} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footnote">
          <ShieldCheck size={18} />
          <div>
            <strong>证据优先</strong>
            <span>无依据时拒答，不替用户猜测。</span>
          </div>
        </div>
      </aside>

      <main className="workspace">
        <div className="workspace-topline">
          <span className={`connection-indicator ${online === true ? "online" : online === false ? "offline" : "checking"}`}>
            <i />
            {online === true ? "API 已连接" : online === false ? "API 未连接" : "正在检查 API"}
          </span>
          <span className="release-label">Enterprise preview · 2026</span>
        </div>
        {view === "chat" ? <ChatPage /> : null}
        {view === "knowledge" ? <KnowledgePage /> : null}
        {view === "evaluation" ? <EvaluationPage /> : null}
        {view === "relations" ? <RelationsPage /> : null}
      </main>
    </div>
  );
}
