import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  BookOpenText,
  GitPullRequestArrow,
  ListChecks,
  MessageSquareText,
  Network,
  PanelRightClose,
  ShieldCheck,
} from "lucide-react";

import { api } from "./lib/api";
import { ChatPage } from "./pages/ChatPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { GraphPage } from "./pages/GraphPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { RelationsPage } from "./pages/RelationsPage";
import { TasksPage, tasksEnabled } from "./pages/TasksPage";
import type { PublicConfig, ViewId } from "./types";

const NAV_ITEMS = [
  { id: "chat" as const, label: "可信问答", icon: MessageSquareText },
  { id: "knowledge" as const, label: "制度台账", icon: BookOpenText },
  { id: "graph" as const, label: "知识图谱", icon: Network },
  { id: "evaluation" as const, label: "质量账本", icon: Activity },
  { id: "relations" as const, label: "关系审核", icon: GitPullRequestArrow },
];

/** 各视图的角色定位（填入 PageHeader eyebrow），帮助用户理解视图边界 */
const VIEW_EYEBROWS: Record<ViewId, string> = {
  chat: "提问 · 治理式问答",
  knowledge: "知识 · 制度材料",
  graph: "关系 · 确认与展示",
  evaluation: "衡量 · 证据质量",
  relations: "裁决 · 人机共治",
};

/** 各视图的核心动作快速链路（填入 PageHeader meta） */
const VIEW_META: Record<ViewId, string[]> = {
  chat: ["可直接开始提问，或按 / 快速聚焦", "回答带来源与版本，可一键导出证据"],
  knowledge: ["上传 · 索引 · 治理一条链", "选择材料查看版本与责任信息"],
  graph: ["所有连线均已由人确认", "滚轮缩放 · 拖拽平移"],
  evaluation: ["每个指标对应一次真实运行", "参考脚本 run_answer_evaluation.py"],
  relations: ["候选不自动进检索", "确认/拒绝都需填写原因"],
};

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
  // M4-A：后台任务面板（探测式入口；flag 关闭时不出现）。
  // 走查修正 X1：一次性探测会在「页面先于 API 就绪」时永远错过入口
  // （本地/容器重启的常见时序）。改为 30s 周期重探直至成功，成功即停。
  const [tasksAvailable, setTasksAvailable] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let settled = false;
    const probe = async () => {
      try {
        const enabled = await tasksEnabled();
        if (enabled && !cancelled) {
          settled = true;
          setTasksAvailable(true);
        }
      } catch {
        /* 探测失败保持 false，下轮重试 */
      }
    };
    void probe();
    const timer = window.setInterval(() => {
      if (settled || cancelled) {
        window.clearInterval(timer);
        return;
      }
      void probe();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

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

  // U5：健康检查可重试——服务后启动时，用户不必刷新整页。
  // 走查修正 X2：加周期监测（45s）——进程卡死（连接超时）时连接指示
  // 自动转灰，并显示全页横幅 + 重试按钮，输入不再石沉大海。
  const [healthFails, setHealthFails] = useState(0);
  const checkHealth = useCallback(async () => {
    setCheckingHealth(true);
    try {
      await api.health();
      setOnline(true);
      setHealthFails(0);
      // 健康时顺带取公开配置；失败不影响连接状态本身
      try {
        setPublicConfig(await api.publicConfig());
      } catch {
        setPublicConfig(null);
      }
    } catch {
      setOnline(false);
      setHealthFails((n) => n + 1);
    } finally {
      setCheckingHealth(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    const timer = window.setInterval(() => void checkHealth(), 45_000);
    return () => window.clearInterval(timer);
  }, [checkHealth]);
  // 连续两次失败 = 服务无响应（区别于瞬时网络抖动）
  const unresponsive = healthFails >= 2;

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
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>
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
        <p className="value-proposition">每个结论带版本、来源与核验，可导出给制度责任人复核。</p>

        {/* M4-A：后台任务入口（Tasks UI-G2：探测式，非第六主导航；
            AGENT_TASKS_ENABLED 关闭（404）时不渲染） */}
        {tasksAvailable ? (
          <button
            className="nav-button tasks-entry"
            onClick={() => setTasksOpen(true)}
            type="button"
          >
            <ListChecks size={18} strokeWidth={1.8} />
            <span>后台核对任务</span>
          </button>
        ) : null}
      </aside>

      {tasksOpen ? (
        <div className="tasks-overlay" role="dialog" aria-label="后台核对任务">
          <div className="tasks-overlay-panel">
            <button
              className="button secondary small tasks-overlay-close"
              onClick={() => setTasksOpen(false)}
              type="button"
              aria-label="关闭后台任务面板"
            >
              <PanelRightClose size={16} />
            </button>
            <TasksPage />
          </div>
        </div>
      ) : null}

      <main className="workspace" id="main-content">
        {/* 走查 X2：服务无响应（连续两次健康检查失败）时的全页横幅——
            进程卡死不再表现为"输入石沉大海" */}
        {unresponsive ? (
          <div className="service-unresponsive" role="alert">
            <span>服务暂时无响应。你的会话和已生成的回答不受影响。</span>
            <button className="button secondary small" disabled={checkingHealth} onClick={() => void checkHealth()} type="button">
              {checkingHealth ? "正在重试…" : "重试连接"}
            </button>
          </div>
        ) : null}
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
