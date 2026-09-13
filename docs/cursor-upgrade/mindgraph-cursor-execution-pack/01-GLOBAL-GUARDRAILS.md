# 全局工程约束

## 1. 先取证，后修改

开始任何 PR 前必须：

- 获取最新 `main` SHA、工作区状态和当前分支。
- 阅读仓库根 `AGENTS.md` 以及目标目录中的约束文件。
- 检查目标文件、相关测试、配置项、数据库 schema、API/SSE 契约。
- 运行最小基线测试并保存结果。
- 若路线图与代码不一致，先报告差异，不得直接重写。

## 2. 渐进式改造

- 一次只解决一个可验证问题。
- 默认保持现有 API、SSE、MCP、索引读取和 SQLite 数据兼容。
- 数据库迁移只允许 additive migration；禁止无迁移直接改表语义。
- 新能力必须有 feature flag、shadow、dual-read/dual-write 或明确回滚路径。
- 不得以“清理代码”为理由删除仍被评测基线或兼容路径使用的模块。

## 3. 安全红线

- ACL、source、文档状态、有效期和版本过滤必须在候选进入模型前生效。
- 无权限资源对非拥有者返回不暴露存在性的错误。
- 不记录 token、API key、完整 Authorization、私有候选正文或未脱敏工具参数。
- 冲突、无证据、无权限时 fail-closed；禁止让 LLM 猜测。
- 不允许 LLM 自主确认关系、修改知识源或绕开应用服务直接读写存储。

## 4. 评测红线

- 不得为了提升数字修改 Golden 标签、降低阈值或丢弃失败样本。
- 评测口径变化必须版本化，并同时保留旧口径结果。
- 所有指标必须绑定 dataset、corpus、index、chunking、embedding、reranker、prompt、provider 和 model 版本。
- 缺失 token/cost 不能记为 0。
- 必须区分：未召回、召回未排序、最终证据正确但生成错误。

## 5. 架构边界

- 当前不引入 LangGraph、CrewAI 或开放式多 Agent。
- 当前不默认开启 Graph；只允许 confirmed 且可回原文的关系参与受控扩展。
- 当前不以迁移 Milvus 解决准确率问题。
- Local Profile 必须继续支持 SQLite + FAISS + BM25 的零外部依赖运行。
- Enterprise Scale 只通过 Protocol/Adapter 增量引入，不污染默认安装。

## 6. 完成定义

每个 PR 都必须同时具备：

1. 失败复现或基线证据；
2. 最小代码修改；
3. 正常、异常、边界、安全测试；
4. 真实命令输出；
5. 指标或行为差异；
6. 文档/配置同步；
7. 可执行回滚说明。

仅“代码能运行”不算完成。
