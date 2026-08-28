import { useCallback, useEffect, useState } from "react";
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
import { GraphPage } from "./pages/GraphPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { RelationsPage } from "./pages/RelationsPage";
import type { PublicConfig, ViewId } from "./types";

const NAV_ITEMS = [
  { id: "chat" as const, label: "可信问答", icon: MessageSquareText },
  { id: "knowledge" as const, label: "制度台账", icon: BookOpenText },
  { id: "graph" as const, label: "知识图谱", icon: Network },
  { id: "evaluation" as const, label: "质量账本", icon: Activity },
  { id: "relations" as const, label: "关系审核", icon: GitPullRequestArrow },
];

const VIEW_IDS: ViewId[] = ["chat", "knowledge", "graph", "evaluation", "relations"];

/** U7：从 location.hash 解析视图（如 #/knowledge），非法值回退 chat */
function viewFromHash(): ViewId {
  const match = window.location.hash.match(/^#\/(\w+)/);
  const candidate = match?.[1] as ViewId | undefined;
  return candidate && VIEW_IDS.includes(candidate) ? candidate : "chat";
}

export function App() {
  // U7：视图状态与 URL hash 双向同步——刷新/分享链接不再丢失所在页面
  const [view, setView] = useState<ViewId>(viewFromHash);
  const [online, setOnline] = useState<boolean | null>(null);
  const [checkingHealth, setCheckingHealth] = useState(false);
  // 研究项⑭：模型/服务状态前置——顶栏连接指示可展示当前生成模型与可用性
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);

  useEffect(() => {
    const onHashChange = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((next: ViewId) => {
    setView(next);
    if (window.location.hash !== `#/${next}`) {
      window.history.replaceState(null, "", `#/${next}`);
    }
  }, []);

  // U5：健康检查可重试——服务后启动时，用户不必刷新整页
  const checkHealth = useCallback(async () => {
    setCheckingHealth(true);
    try {
      await api.health();
      setOnline(true);
      // 健康时顺带取公开配置；失败不影响连接状态本身
      try {
        setPublicConfig(await api.publicConfig());
      } catch {
        setPublicConfig(null);
      }
    } catch {
      setOnline(false);
    } finally {
      setCheckingHealth(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  // P5：键盘效率——1-5 切视图，/ 聚焦提问框（输入控件内不触发）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)
      ) {
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key >= "1" && event.key <= "5") {
        navigate(VIEW_IDS[Number(event.key) - 1]);
      } else if (event.key === "/") {
        event.preventDefault();
        navigate("chat");
        window.setTimeout(() => document.getElementById("chat-composer")?.focus(), 0);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            {/* 品牌标「核实盖章」完整版：印章框 + 文档折角 + 对勾；currentColor 随主题适配（≥24px 层级） */}
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" focusable="false">
              <path d="M8.6 5.4 H15.6 L19.4 9.2 V16.2 Q19.4 18.6 17 18.6 H8.6 Q6.2 18.6 6.2 16.2 V7.8 Q6.2 5.4 8.6 5.4 Z" />
              <path d="M15.6 5.4 V9.2 H19.4" />
              <path d="M9.2 12.8 l2.3 2.3 4.3-4.8" />
            </svg>
          </span>
          <div>
            <strong>MindGraph</strong>
            <span>依据工作台</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="主导航">
          {NAV_ITEMS.map((item, navIndex) => {
            const Icon = item.icon;
            return (
              <button
                className={view === item.id ? "nav-button active" : "nav-button"}
                key={item.id}
                onClick={() => navigate(item.id)}
                /* 研究项⑤：去掉常驻编号角标，快捷键改为悬浮提示 */
                title={`快捷键 ${navIndex + 1}`}
                type="button"
              >
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
          {/* 研究项⑭：连接指示同时披露当前生成模型，未配置/不可用时前置提醒，而不是等提问后才发现 */}
          {(() => {
            const models = publicConfig?.chat_models ?? [];
            const provider = publicConfig?.default_chat_provider;
            const current = models.find((item) => item.provider === provider) ?? models[0] ?? null;
            const modelLabel = current ? `${current.provider} · ${current.model}` : null;
            const modelReady = current ? current.configured !== false : null;
            const tooltip = online === true && modelLabel
              ? `生成模型：${modelLabel}${current?.verified ? "（已验证）" : ""}${modelReady === false ? " · 未配置/不可用" : ""}`
              : undefined;
            return (
              <span
                className={`connection-indicator ${online === true ? "online" : online === false ? "offline" : "checking"}`}
                title={tooltip}
              >
                <i />
                {/* UI 审计 #13：开发向"API"措辞改为面向用户的"服务" */}
                {online === true ? "服务正常" : online === false ? "服务连接失败" : "正在检查服务"}
                {online === true && modelLabel ? (
                  <span className={modelReady === false ? "connection-model degraded" : "connection-model"}>
                    {modelLabel}
                    {modelReady === false ? " · 未配置" : ""}
                  </span>
                ) : null}
              </span>
            );
          })()}
          {/* U5：离线时提供重试入口，覆盖"服务晚于页面启动"的常见场景 */}
          {online === false ? (
            <button className="connection-retry" disabled={checkingHealth} onClick={() => void checkHealth()} type="button">
              {checkingHealth ? "正在重连…" : "重试连接"}
            </button>
          ) : null}
        </div>
        {view === "chat" ? <ChatPage /> : null}
        {view === "knowledge" ? <KnowledgePage /> : null}
        {view === "graph" ? <GraphPage /> : null}
        {view === "evaluation" ? <EvaluationPage /> : null}
        {view === "relations" ? <RelationsPage /> : null}
      </main>
    </div>
  );
}
