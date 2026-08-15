/* ══════════════════════════════════════════════════════════════
   小财 · 报销政策助手 — Web UI Application Logic
   SPA with SSE streaming chat, 4 views, real-time retrieval trace
   ══════════════════════════════════════════════════════════════ */

(() => {
  "use strict";

  // ── Config ──
  const DEFAULT_API_BASE =
    new URLSearchParams(location.search).get("api") ||
    "http://localhost:8000/api/v1";

  // ── State ──
  const state = {
    apiBase: DEFAULT_API_BASE,
    theme: localStorage.getItem("rag-theme") || "dark",
    currentView: "chat",
    messages: [],
    isStreaming: false,
    currentAnswerText: "",
    retrievalTrace: null,
    performanceData: {},
    documents: [],
    versions: [],
    evalRuns: [],
    badCases: [],
    configPublic: null,
    conversationId: null,
  };

  // ── DOM References ──
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  // ── Theme Toggle ──
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    state.theme = t;
    localStorage.setItem("rag-theme", t);
    $(".theme-icon-dark").style.display = t === "dark" ? "" : "none";
    $(".theme-icon-light").style.display = t === "light" ? "" : "none";
  }
  applyTheme(state.theme);
  $("#theme-toggle-btn").addEventListener("click", () =>
    applyTheme(state.theme === "dark" ? "light" : "dark")
  );

  // ── Navigation ──
  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  function switchView(viewName) {
    state.currentView = viewName;
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === viewName));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${viewName}`));
    $("#strategy-preset").style.display = viewName === "chat" ? "" : "none";
    $("#trace-panel").style.display = viewName === "chat" ? "" : "none";
    // Load view-specific data
    if (viewName === "knowledge") loadKnowledge();
    if (viewName === "evaluation") loadEvaluations();
    if (viewName === "feedback") loadBadCases();
  }

  // ── Toast Helper ──
  function toast(msg, type = "") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("#toast-container").appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  // ── API Client ──
  async function api(path, opts = {}) {
    const url = `${state.apiBase}${path}`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...opts.headers },
      ...opts,
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).detail || res.statusText; } catch { detail = res.statusText; }
      throw new Error(`API ${path} ${res.status}: ${detail}`);
    }
    return res;
  }

  async function apiJSON(path, opts = {}) {
    return (await api(path, opts)).json();
  }

  // ── Health Check & Status ──
  async function checkHealth() {
    try {
      const d = await apiJSON("/health");
      updateApiStatus(true, d.service || "OK");
      // Load public config
      state.configPublic = await apiJSON("/config/public").catch(() => null);
    } catch {
      updateApiStatus(false, "离线");
    }
  }
  function updateApiStatus(online, label) {
    const dot = $("#api-status-dot"),
      txt = $("#api-status-text");
    dot.className = `status-dot ${online ? "status-online" : "status-offline"}`;
    txt.textContent = label;
  }

  // ── Chat: Build config from controls ──
  function getChatConfig() {
    const cats = [...$$(".chip.active")].map((c) => c.dataset.cat).filter(Boolean);
    return {
      question: "",
      retrieval_strategy: $("#sel-strategy").value,
      knowledge_categories: cats.length ? cats : undefined,
      query_date: $("#query-date").value || undefined,
      include_historical: $("#chk-history").checked,
      final_top_k: parseInt($("#input-topk").value, 10) || 5,
      conversation_id: state.conversationId || undefined,
      chat_provider: undefined, // let server decide
    };
  }

  // ── Category Chips Toggle ──
  $$(".chip").forEach((c) =>
    c.addEventListener("click", () => c.classList.toggle("active"))
  );

  // ── Send Message (SSE Streaming) ──
  async function sendMessage() {
    const input = $("#msg-input");
    const q = input.value.trim();
    if (!q || state.isStreaming) return;

    // Add user bubble
    addMessageBubble("user", q);
    input.value = "";
    input.style.height = "auto";

    // Hide welcome message on first message
    const welcome = $(".welcome-message");
    if (welcome) welcome.style.display = "none";

    state.isStreaming = true;
    state.currentAnswerText = "";
    $("#typing-indicator").style.display = "flex";
    $("#btn-send").disabled = true;

    // Create AI answer placeholder
    const aiRow = createAIBubble();
    $("#message-stream").appendChild(aiRow);
    scrollToBottom();

    // Reset trace panel
    resetTimeline();

    const config = getChatConfig();
    config.question = q;

    try {
      const resp = await fetch(`${state.apiBase}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      await parseSSE(resp.body, aiRow);
    } catch (err) {
      console.error("Stream error:", err);
      appendAnswerText(aiRow, `\n\n⚠️ 连接失败：${err.message}`);
    } finally {
      state.isStreaming = false;
      $("#typing-indicator").style.display = "none";
      $("#btn-send").disabled = false;
      markAllStepsDone(); // ensure timeline completes
    }
  }

  // ── SSE Parser (POST-based, uses ReadableStream) ──
  async function parseSSE(body, aiRow) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let event = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || ""; // keep incomplete tail

      for (const line of lines) {
        const trimmed = line.replace(/\r$/, "");
        if (trimmed.startsWith("event:")) {
          event = trimmed.slice(6).trim();
        } else if (trimmed.startsWith("data:")) {
          try {
            handleSSEEvent(event, JSON.parse(trimmed.slice(5).trim()), aiRow);
          } catch (_) {}
        } else if (trimmed === "") {
          event = "";
        }
      }
    }
  }

  // ── SSE Event Handler ──
  function handleSSEEvent(evt, data, aiRow) {
    switch (evt) {
      case "request_started":
        setStepStatus("retrieve", "running");
        break;

      case "scope_check_completed":
        if (data.out_of_scope) appendAnswerText(aiRow, "⚠️ 该问题超出报销政策知识范围。\n\n");
        else setStepStatus("retrieve", "done");
        break;

      case "retrieval_started":
        setStepStatus("dense", "running");
        setStepStatus("bm25", "running");
        break;

      case "retrieval_completed":
        setStepStatus("dense", "done");
        setStepStatus("bm25", "done");
        setStepStatus("fusion", "running");
        if (data.candidate_counts) updateCandidateChart(data.candidate_counts);
        break;

      case "rerank_completed":
        setStepStatus("fusion", "done");
        setStepStatus("rerank", "done");
        if (data.candidate_counts) updateCandidateChart(data.candidate_counts);
        break;

      case "degraded":
        showDegradedBadge(data.reason);
        break;

      case "generation_started":
        setStepStatus("generate", "running");
        break;

      case "answer_delta":
        appendAnswerText(aiRow, data.text || "");
        scrollToBottom();
        break;

      case "citations":
        renderCitations(aiRow, data.citations || []);
        break;

      case "usage":
        updatePerformance(data);
        break;

      case "completed":
        setStepStatus("generate", "done");
        if (data.timing) updateTiming(data.timing);
        if (data.conversation_id) state.conversationId = data.conversation_id;
        break;

      case "error":
        appendAnswerText(aiRow, `\n\n❌ 错误 [${data.code}]: ${data.message}`);
        setStepError();
        break;
    }
  }

  // ── Chat UI Helpers ──
  function addMessageBubble(role, text) {
    state.messages.push({ role, text, ts: Date.now() });
    const row = document.createElement("div");
    row.className = `msg-row msg-${role}`;
    row.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
    $("#message-stream").appendChild(row);
    scrollToBottom();
  }

  function createAIBubble() {
    const row = document.createElement("div");
    row.className = "msg-row msg-ai";
    row.innerHTML = `
      <div class="msg-bubble">
        <div class="answer-body"></div>
        <div class="citations-section"></div>
      </div>`;
    return row;
  }

  function appendAnswerText(aiRow, text) {
    state.currentAnswerText += text;
    const body = aiRow.querySelector(".answer-body");
    if (!body) return;
    // Simple markdown-like formatting
    body.innerHTML = formatMarkdown(state.currentAnswerText);
  }

  function formatMarkdown(text) {
    return escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^(\d+[\.\)]\s+.+)/gm, (m) => `<div style="margin-bottom:4px">${m}</div>`)
      .replace(/\n/g, "<br>");
  }

  function renderCitations(aiRow, citations) {
    const section = aiRow.querySelector(".citations-section");
    if (!section || !citations.length) return;
    section.innerHTML = `
      <span class="citations-toggle">📎 引用来源 (${citations.length})</span>
      <div class="citation-list">
        ${citations.map((c) => `
          <div class="citation-item">
            <strong>${escapeHtml(c.document_title || c.document_id || "文档")}</strong>
            <p>${escapeHtml(c.chunk_text?.slice(0, 160) || c.content?.slice(0, 160) || "")}...</p>
            <div class="citation-meta">
              <span>权威等级: ${c.authority_level || "--"}</span>
              <span>分类: ${c.knowledge_category || "--"}</span>
            </div>
            <div class="confidence-bar"><div class="confidence-fill" style="width:${Math.round(((c.retrieval_score || 0) * 100))}%"></div></div>
          </div>`).join("")}
      </div>`;
    // Toggle expand/collapse for citations
    section.querySelector(".citations-toggle")?.addEventListener("click", () => {
      const list = section.querySelector(".citation-list");
      list.style.display = list.style.display === "none" ? "" : "none";
    });
  }

  function scrollToBottom() {
    const ms = $("#message-stream");
    requestAnimationFrame(() => (ms.scrollTop = ms.scrollHeight));
  }

  // ── Trace Panel / Timeline ──
  function resetTimeline() {
    $$(".tl-step").forEach((el) => {
      el.querySelector(".tl-dot").className = "tl-dot pending";
      el.querySelector(".tl-time").textContent = "-- ms";
    });
    $("#degraded-badge").style.display = "none";
    $("#perf-ttft").textContent = "-- ms";
    $("#perf-total").textContent = "-- ms";
    $("#perf-prompt").textContent = "--";
    $("#perf-completion").textContent = "--";
    $("#perf-strategy").textContent = "--";
  }

  function setStepStatus(step, status) {
    const el = $(`.tl-step[data-step="${step}"]`);
    if (!el) return;
    const dot = el.querySelector(".tl-dot");
    dot.className = `tl-dot ${status}`;
    if (status === "done") dot.classList.add("done");
    if (status === "error") dot.classList.add("error");
  }

  function setStepError() {
    $$(".tl-dot.pending").forEach((d) => d.classList.add("error"));
  }

  function markAllStepsDone() {
    $$(".tl-dot.pending").forEach((d) => {
      d.classList.remove("pending");
      d.classList.add("done");
    });
  }

  function showDegradedBadge(reason) {
    const badge = $("#degraded-badge");
    badge.style.display = "inline-block";
    badge.textContent = reason ? `已降级: ${reason}` : "已降级";
  }

  // ── Candidate Chart (Canvas bar chart) ──
  function updateCandidateChart(counts) {
    const canvas = $("#candidate-chart");
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const labels = ["dense", "bm25", "fused", "reranked"];
    const values = [
      counts.dense || 0,
      counts.bm25 || 0,
      counts.fusion || 0,
      counts.reranked || 0,
    ];
    const maxVal = Math.max(...values, 1);
    const colors = ["#6366F1", "#F59E0B", "#38BDF8", "#10B981"];

    const barW = 44, gap = 16, startX = 24, maxH = 80, baseY = 100;

    values.forEach((v, i) => {
      const h = Math.max((v / maxVal) * maxH, 4);
      const x = startX + i * (barW + gap);
      ctx.fillStyle = colors[i];
      roundRect(ctx, x, baseY - h, barW, h, 5);
      // Value label
      ctx.fillStyle = getComputedStyle(document.documentElement)
        .getPropertyValue("--text-secondary")
        .trim() || "#94A3B8";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(v, x + barW / 2, baseY - h - 5);
      // X-axis label
      ctx.fillText(labels[i], x + barW / 2, baseY + 14);
    });

    // Update legend
    $("#chart-legend").innerHTML = labels
      .map(
        (l, i) =>
          `<span><i style="color:${colors[i]};font-style:normal">●</i> ${l}</span>`
      )
      .join("");
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
    ctx.fill();
  }

  // ── Performance Panel ──
  function updatePerformance(usage) {
    if (!usage) return;
    $("#perf-prompt").textContent = usage.prompt_tokens ?? usage.prompt_tokens_used ?? "--";
    $("#perf-completion").textContent =
      usage.completion_tokens ?? usage.completion_tokens_used ?? "--";
  }

  function updateTiming(timing) {
    if (!timing) return;
    $("#perf-ttft").textContent = timing.ttft_ms != null ? `${timing.ttft_ms} ms` : "-- ms";
    $("#perf-total").textContent = timing.total_ms != null ? `${timing.total_ms} ms` : "-- ms";
    // Update timeline steps with actual times
    if (timing.embedding_ms) updateTimeLabel("dense", timing.embedding_ms);
    if (timing.sparse_retrieval_ms) updateTimeLabel("bm25", timing.sparse_retrieval_ms);
    if (timing.fusion_ms) updateTimeLabel("fusion", timing.fusion_ms);
    if (timing.reranker_ms) updateTimeLabel("rerank", timing.reranker_ms);
    if (timing.generation_ms) updateTimeLabel("generate", timing.generation_ms);
  }

  function updateTimeLabel(step, ms) {
    const el = $(`.tl-step[data-step="${step}"] .tl-time`);
    if (el) el.textContent = `${ms} ms`;
  }

  // ── Knowledge View ──
  async function loadKnowledge() {
    const grid = $("#knowledge-grid");
    grid.innerHTML = Array(4)
      .fill('<div class="skeleton-card"></div>')
      .join("");
    try {
      state.documents = await apiJSON("/knowledge/documents");
      renderDocuments(state.documents);
      loadIndexStatus();
      loadVersions();
    } catch (e) {
      grid.innerHTML = `<div class="empty-state">加载失败: ${e.message}</div>`;
    }
  }

  function renderDocuments(docs) {
    const grid = $("#knowledge-grid");
    if (!docs.length) {
      grid.innerHTML = '<div class="empty-state">暂无文档，点击"上传文档"添加</div>';
      return;
    }
    grid.innerHTML = docs.map((d) => `
      <div class="doc-card">
        <h4>${escapeHtml(d.title || d.filename || d.document_id || "Untitled")}</h4>
        <div class="doc-meta">
          分类: ${d.category || "--"} | 状态: ${d.status || "active"}
        </div>
        <div class="doc-meta">创建: ${(d.created_at || "").slice(0, 10)} | ID: ${d.document_id?.slice(0, 12)}...</div>
        <button class="action-btn ghost mt-2" onclick="window.deleteDoc('${d.document_id}')">删除</button>
      </div>`).join("");
  }

  async function loadIndexStatus() {
    try {
      const s = await apiJSON("/knowledge/index/status");
      $("#index-status-card").style.display = "";
      $("#index-status-body").innerHTML = `
        <p>状态: <strong>${s.status || "unknown"}</strong></p>
        <p>文档数: <strong>${s.document_count ?? "--"}</strong></p>
        <p>Chunk 数: <strong>${s.chunk_count ?? "--"}</strong></p>
        <p>最后构建: <strong>${s.last_built_at || "--"}</strong></p>`;
    } catch (_) {}
  }

  async function loadVersions() {
    try {
      state.versions = await apiJSON("/knowledge/versions");
      const tbody = $("#versions-tbody");
      tbody.innerHTML = state.versions.map((v) => `
        <tr>
          <td><code>${v.document_id?.slice(0,14) || v.id?.slice(0, 14) || "-"}</code></td>
          <td><span class="run-status ${v.status || "draft"}">${v.status || "draft"}</span></td>
          <td>${v.category || "--"}</td>
          <td>${v.authority_level || "--"}</td>
          <td><button class="action-btn ghost" onclick="window.transitionDoc('${v.document_id}', 'approved')">审批</button></td>
        </tr>`).join("");
    } catch (_) {}
  }

  // Upload handler
  $("#btn-upload-doc").addEventListener("click", () =>
    $("#upload-form").style.display = ""
  );
  $("#btn-cancel-upload").addEventListener("click", () =>
    $("#upload-form").style.display = "none"
  );
  $("#upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fileInput = $("#upload-file");
    const file = fileInput.files[0];
    if (!file) return toast("请选择文件", "error");

    const formData = new FormData();
    formData.append("file", file);
    formData.append("category", $("#upload-category").value);

    try {
      await fetch(`${state.apiBase}/knowledge/documents`, {
        method: "POST",
        body: formData,
      });
      toast("文档上传成功", "success");
      $("#upload-form").style.display = "none";
      loadKnowledge();
    } catch (err) {
      toast(`上传失败: ${err.message}`, "error");
    }
  });

  // Rebuild index
  $("#btn-rebuild-index").addEventListener("click", async () => {
    try {
      await api("/knowledge/index/rebuild", { method: "POST" });
      toast("索引重建任务已提交", "success");
      loadIndexStatus();
    } catch (err) {
      toast(`重建失败: ${err.message}`, "error");
    }
  });

  // Expose delete/transition to global scope (for inline onclick)
  window.deleteDoc = async (id) => {
    if (!confirm("确认删除此文档？")) return;
    try {
      await api(`/knowledge/documents/${id}`, { method: "DELETE" });
      toast("文档已删除", "success");
      loadKnowledge();
    } catch (err) {
      toast(`删除失败: ${err.message}`, "error");
    }
  };
  window.transitionDoc = async (id, target) => {
    try {
      await api(`/knowledge/versions/${id}/transition?target=${target}`, {
        method: "POST",
      });
      toast("状态流转成功", "success");
      loadVersions();
    } catch (err) {
      toast(`操作失败: ${err.message}`, "error");
    }
  };

  // ── Evaluation View ──
  async function loadEvaluations() {
    const list = $("#eval-runs-list");
    list.innerHTML = '<div class="empty-state">加载中...</div>';
    try {
      state.evalRuns = await apiJSON("/evaluations/runs");
      renderEvalRuns(state.evalRuns);
      // Try to load latest summary metrics
      if (state.evalRuns.length > 0) loadEvalSummary(state.evalRuns[0].run_id);
    } catch (e) {
      list.innerHTML = `<div class="empty-state">加载失败: ${e.message}</div>`;
    }
  }

  function renderEvalRuns(runs) {
    const list = $("#eval-runs-list");
    if (!runs.length) {
      list.innerHTML = '<div class="empty-state">暂无评测记录</div>';
      return;
    }
    list.innerHTML = runs.map((r) => `
      <div class="run-card" onclick='window.showEvalDetail("${r.run_id}")'>
        <div class="run-id">${r.run_id.slice(0, 20)}...</div>
        <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
          <span class="run-status ${r.status || "unknown"}">${r.status || "unknown"}</span>
          <span style="font-size:11px;color:var(--text-muted)">${(r.created_at || "").slice(0, 19)}</span>
          <span style="font-size:11px;color:var(--text-muted)">策略: ${r.retrieval_strategy || "--"}</span>
        </div>
      </div>`).join("");
  }

  async function loadEvalSummary(runId) {
    try {
      const detail = await apiJSON(`/evaluations/runs/${runId}`);
      const sm = detail.summary_metrics || {};
      updateKPICard(0, sm.recall_at_5 ?? sm["recall@5"] ?? "--");
      updateKPICard(1, sm.mrr ?? "--");
      updateKPICard(2, sm.document_hit_rate ?? "--");
      updateKPICard(3, sm.chunk_hit_rate ?? "--");
    } catch (_) {}
  }

  function updateKPICard(idx, val) {
    const cards = $$("#eval-kpi-cards .kpi-value");
    if (cards[idx]) cards[idx].textContent = val;
  }

  window.showEvalDetail = async (runId) => {
    try {
      const detail = await apiJSON(`/evaluations/runs/${runId}`);
      $("#eval-detail-body").innerHTML = `
        <pre style="background:var(--bg-base);padding:12px;border-radius:var(--radius-sm);overflow:auto;font-size:12px;max-height:400px">${escapeHtml(JSON.stringify(detail, null, 2))}</pre>`;
      $("#eval-detail-modal").style.display = "flex";
    } catch (e) {
      toast(`加载详情失败: ${e.message}`, "error");
    }
  };
  $("#btn-close-eval-detail").addEventListener("click", () =>
    $("#eval-detail-modal").style.display = "none"
  );

  // Run evaluation
  $("#btn-run-eval").addEventListener("click", async () => {
    try {
      await api("/evaluations/runs", { method: "POST" });
      toast("评测任务已提交", "success");
      loadEvaluations();
    } catch (err) {
      toast(`提交失败: ${err.message}`, "error");
    }
  });

  // ── Feedback View ──
  $$(".tab[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".tab-panel").forEach((p) => (p.style.display = p.id.includes(tab.dataset.tab) ? "" : "none"));
    });
  });

  async function loadBadCases() {
    const list = $("#badcases-list");
    list.innerHTML = '<div class="empty-state">加载中...</div>';
    try {
      state.badCases = await apiJSON("/feedback/bad-cases");
      if (!state.badCases.length) {
        list.innerHTML = '<div class="empty-state">暂无 Bad Case 记录 — 太好了！</div>';
        return;
      }
      list.innerHTML = state.badCases.map((b) => `
        <div class="run-card">
          <div>ID: <code>${b.bad_case_id?.slice(0, 18) || b.id?.slice(0, 18) || "?"}</code></div>
          <div style="margin-top:4px;font-size:12px">
            问题: ${b.question_text?.slice(0, 100) || b.question?.slice(0, 100) || "--"}
          </div>
          <div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">
            <span class="run-status ${b.status || "new"}">${b.status || "new"}</span>
            <span style="font-size:11px;color:var(--text-muted)">${b.error_category || "--"}</span>
          </div>
        </div>`).join("");
    } catch (e) {
      list.innerHTML = `<div class="empty-state">加载失败: ${e.message}</div>`;
    }
  }

  $("#feedback-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const textarea = form.querySelector("textarea");
    const selects = form.querySelector("select");
    const comment = textarea.value.trim();
    if (!comment) return toast("请描述问题内容", "error");

    const codes = [...selects.selectedOptions].map((o) => o.value);
    try {
      await api("/feedback/feedback", {
        method: "POST",
        body: JSON.stringify({
          rating: "not_helpful",
          reason_codes: codes,
          comment,
        }),
      });
      toast("反馈提交成功，感谢你的帮助！", "success");
      textarea.value = "";
      selects.selectedIndex = -1;
      loadBadCases();
    } catch (err) {
      toast(`提交失败: ${err.message}`, "error");
    }
  });

  // ── Quick-start hints click-to-send ──
  $$(".hint-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("#msg-input").value = chip.textContent.split(" ").slice(1).join(" ");
      sendMessage();
    });
  });

  // ── Input Area: Enter to send, Shift+Enter for newline ──
  $("#msg-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("#btn-send").addEventListener("click", sendMessage);

  // Auto-resize textarea
  $("#msg-input").addEventListener("input", () => {
    const ta = $("#msg-input");
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  });

  // ── Escape HTML ──
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Initialize ──
  async function init() {
    await checkHealth();
    // Poll health every 30s
    setInterval(checkHealth, 30000);
  }

  init().catch(console.error);
})();
