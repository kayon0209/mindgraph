const { Plugin, Notice, ItemView, MarkdownView } = require("obsidian");
const { buildApiHeaders, reduceMindGraphEvent } = require("./sse-events");

const VIEW_TYPE = "mindgraph-view";

class MindGraphView extends ItemView {
  constructor(leaf) {
    super(leaf);
    this.apiBase = "http://127.0.0.1:8000/api/v1";
    this.apiKey = "";
    this.graphEnabled = true;
    this.answer = "";
  }

  getViewType() {
    return VIEW_TYPE;
  }

  getDisplayText() {
    return "MindGraph";
  }

  getIcon() {
    return "network";
  }

  async onOpen() {
    const el = this.containerEl;
    el.empty();
    el.addClass("mg-view");

    el.createEl("h3", { text: "🧠 MindGraph 问答", cls: "mg-title" });

    const bar = el.createDiv({ cls: "mg-bar" });
    const toggle = bar.createEl("label", { cls: "mg-toggle" });
    const cb = toggle.createEl("input", { type: "checkbox" });
    cb.checked = this.graphEnabled;
    cb.onchange = () => (this.graphEnabled = cb.checked);
    toggle.createSpan({ text: " 图谱扩展" });
    const base = bar.createEl("input", {
      type: "text",
      cls: "mg-base",
      value: this.apiBase,
    });
    base.onchange = () => (this.apiBase = base.value.trim().replace(/\/+$/, ""));
    const apiKey = bar.createEl("input", {
      type: "password",
      cls: "mg-api-key",
      placeholder: "API Key（仅本次会话）",
    });
    apiKey.oninput = () => (this.apiKey = apiKey.value);

    const input = el.createEl("textarea", {
      cls: "mg-input",
      placeholder: "输入问题，Ctrl/Cmd+Enter 发送…",
    });
    input.rows = 3;

    const btns = el.createDiv({ cls: "mg-btns" });
    const ask = btns.createEl("button", { text: "提问", cls: "mg-primary" });
    const insert = btns.createEl("button", {
      text: "插入到当前笔记",
      cls: "mg-secondary",
    });
    insert.disabled = true;

    const answer = el.createDiv({ cls: "mg-answer" });
    const refs = el.createDiv({ cls: "mg-refs" });

    const send = async () => {
      const q = input.value.trim();
      if (!q) return;
      ask.disabled = true;
      insert.disabled = true;
      answer.setText("⏳ 思考中…");
      refs.empty();
      this.answer = "";
      try {
        await this._stream(q, answer, refs, insert);
      } catch (e) {
        new Notice("MindGraph 错误：" + e.message);
        answer.setText("⚠️ " + e.message);
      } finally {
        ask.disabled = false;
      }
    };

    ask.onclick = send;
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        send();
      }
    });
    insert.onclick = () => this._insert(insert);
  }

  async _stream(q, answerEl, refsEl, insertBtn) {
    const url = `${this.apiBase}/mindgraph/chat/stream`;
    const resp = await fetch(url, {
      method: "POST",
      headers: buildApiHeaders(this.apiKey),
      body: JSON.stringify({ question: q, graph_enabled: this.graphEnabled }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (!resp.body) throw new Error("当前环境不支持流式响应");

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let state = { answer: "", citations: [], graphLinks: [] };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n");
      buf = parts.pop();
      for (const raw of parts) {
        const line = raw.trim();
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json || json === "[DONE]") continue;
        let ev;
        try {
          ev = JSON.parse(json);
        } catch (_) {
          continue;
        }
        state = reduceMindGraphEvent(state, ev);
        this.answer = state.answer;
        if (this.answer) answerEl.setText(this.answer);
      }
    }

    answerEl.setText(this.answer || "(无回答)");
    const { citations, graphLinks } = state;
    if (citations.length || graphLinks.length) {
      refsEl.empty();
      refsEl.createEl("div", { cls: "mg-ref-title", text: "引用 / 关系" });
      citations.forEach((c, i) => {
        const name = c.document_name || c.title || c.source || "";
        refsEl.createEl("div", { cls: "mg-ref", text: `[${i + 1}] ${name}` });
      });
      graphLinks.forEach((g) => {
        const s = g.source_title || g.source || g.source_note_id || "";
        const t = g.target_title || g.target || g.target_note_id || "";
        refsEl.createEl("div", {
          cls: "mg-ref mg-graph",
          text: `↳ 关联：${s} → ${t} (${g.relation_type || "related_to"})`,
        });
      });
    }
    insertBtn.disabled = !this.answer;
  }

  _insert(btn) {
    if (!this.answer) return;
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view || !view.editor) {
      new Notice("请先打开一个 Markdown 笔记再插入");
      return;
    }
    const block = `\n\n> [!note] MindGraph 回答\n> ${this.answer.replace(/\n/g, "\n> ")}\n`;
    view.editor.replaceSelection(block);
    new Notice("已插入到当前笔记");
  }

  async onClose() {
    this.containerEl.empty();
  }
}

class MindGraphPlugin extends Plugin {
  async onload() {
    this.registerView(VIEW_TYPE, (leaf) => new MindGraphView(leaf));
    this.addRibbonIcon("network", "MindGraph 问答", () => this._activate());
    this.addCommand({
      id: "open",
      name: "打开 MindGraph 问答",
      callback: () => this._activate(),
    });
  }

  async _activate() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    workspace.revealLeaf(leaf);
  }

  onunload() {}
}

module.exports = MindGraphPlugin;
