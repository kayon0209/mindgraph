# MindGraph Web

MindGraph 的企业 Web 客户端，与 FastAPI、Obsidian 插件同仓发布。它服务于四个明确任务：可信问答、制度浏览、评测对比、候选关系审核。

## 目录约定

- `src/pages/`：一页对应一个用户目标，不放跨页基础组件。
- `src/components/`：可复用展示组件；组件名使用 PascalCase。
- `src/lib/`：API、SSE 和纯工具函数；不得依赖 React 状态。
- `src/types.ts`：后端契约的 TypeScript 映射，不复制页面局部类型。
- `src/styles.css`：设计 token 和全局布局；不引入另一套组件主题系统。
- 测试与被测文件同目录，命名 `*.test.ts(x)`。

## 运行

```powershell
pnpm install
pnpm dev
```

Vite 开发服务器把 `/api` 代理到 `http://127.0.0.1:8000`。生产构建使用 `VITE_API_BASE_URL`，默认同源 `/api/v1`。

## 设计边界

- 后端返回空数据时展示明确空状态，不填充 Mock 数字。
- 关系主标签始终使用文档标题，UUID 只在详情中作为技术元数据展示。
- 评测指标必须实际渲染；不能只请求后丢弃。
- SSE 进度只响应后端真实事件，不使用定时器伪造阶段完成。
- 新页面先说明用户目标，再决定是否增加导航入口。
