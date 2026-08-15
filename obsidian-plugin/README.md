# MindGraph Obsidian 插件

在 Obsidian 右侧栏直接调用本地 MindGraph API 做 Graph RAG 问答，并把答案一键插入当前笔记。
这是 MindGraph「方案 A 双前端」中的 Obsidian 端（另一端是 React Web Demo）。

## 安装
1. 进入你的 Vault 配置目录 `.obsidian/plugins/`
2. 把本目录（`obsidian-plugin/`）整体复制为 `.obsidian/plugins/mindgraph/`
   （需包含 `manifest.json`、`main.js`、`styles.css`）
3. 重启 Obsidian（或关闭再打开「安全模式」）
4. 设置 → 社区插件 → 已安装中启用 **MindGraph**

## 使用
- 右侧栏点击 MindGraph 图标，或命令面板运行「打开 MindGraph 问答」
- 输入问题，勾选「图谱扩展」启用一跳关系补充证据
- `Ctrl/Cmd + Enter` 发送
- 「插入到当前笔记」把答案作为引用块（callout）追加到当前 Markdown 笔记

## 前置条件
- 本地 MindGraph 后端已在 `http://127.0.0.1:8000` 运行：
  ```bash
  uvicorn api.main:app --app-dir src --port 8000
  ```
- 默认 API 地址可在插件顶部的输入框修改（例如改为 `http://localhost:8000/api/v1`）
- 后端 `.env` 的 `CORS_ORIGINS` 需包含插件来源（本地开发建议放行 `*`）

## 说明
- 插件通过浏览器 `fetch` 消费 `/api/v1/mindgraph/chat/stream` 的 SSE 流，逐字渲染答案
- 关系证据来自 `note_relations` 表（confirmed 关系），与 Web Demo 共享同一后端
