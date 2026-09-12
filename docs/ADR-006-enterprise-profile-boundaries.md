# ADR-006：Enterprise Profile 边界与替换触发阈值

状态：Proposed（PR-15）
日期：2026-09-12

## 背景与动机

Local Profile（SQLite + FAISS + BM25 + 本地文件 + agent_tasks 表）当前被应用服务
**直接依赖**：`TaskService.__init__(database: ProductDatabase)` 吃具体类型，
队列 SQL（claim/lease/幂等）散落在 task_service / task_worker 两处。这使
"几百 GB 语料、多租户、多 worker"场景下独立扩展任何一环都必须改业务代码。

PR-15 的目标**不是接入任何外部服务**，而是建立可替换边界：

| 边界 | Protocol | Local 实现 |
|---|---|---|
| 任务队列 | `domain.storage_ports.TaskQueue` | `infrastructure.sqlite_task_queue.SqliteTaskQueue` |
| 文档正文 | `DocumentStore` | 版本化目录（document_versions 源文件） |
| 结构化元数据 | `MetadataStore` | SQLite（ProductDatabase） |
| 稠密索引 | `VectorIndex` | FAISS（retrieval.types.DenseRetriever 同构） |
| 稀疏索引 | `SparseIndex` | BM25（retrieval.types.SparseRetriever 同构） |

检索/生成/解析三面已有 Protocol（`retrieval.types` / `ChatProvider` /
`DocumentParser`），本 ADR 不重复定义，只补存储与队列缺口。

## 与 ADR-005 非目标清单的关系（显式对齐，避免两份 ADR 矛盾）

ADR-005（单节点硬化）明确列出本轮不做：**多 worker/外部队列、K8s 部署、
正式生产规模声明**。本 ADR **不推翻该清单**——它改变的是"不做"的形态：

- ADR-005 说的"不做" = **不部署、不运维、不对外声明已支持**；
- 本 ADR 做的 = **代码层留出接缝**（Protocol + adapter + 契约测试），
  使未来某项成为必要时，替换发生在 adapter 一层，不动应用服务。

也即：本 ADR 落地后，系统**仍然是单节点 Local Profile**，
Product Signal 里任何"enterprise ready"表述仍属 UNVALIDATED。
外部服务（PostgreSQL/Milvus/OpenSearch/外部队列）**一项都不接入**，
默认安装零外部依赖的承诺不变。

## 替换触发阈值（什么时候才允许接外部实现）

红线：**指标先证明瓶颈，再替换**（与 UPGRADE_PLAN "不把换向量库当优化捷径"
一致）。以下为进入评估的必要条件（非充分）：

1. **VectorIndex → 分布式向量库**（如 Milvus）：单索引体积 ≥ 100 GB，
   或 P95 dense 检索延迟在评测基线中连续两个版本劣化 >30%，
   且已排除分片/过滤/召回层原因。
2. **MetadataStore → PostgreSQL**：SQLite 写锁竞争在真实负载下
   （`database is locked` 重试率 >1%）持续出现，且多节点部署需求被产品拍板。
3. **SparseIndex → OpenSearch**：BM25 语料规模 ≥ 千万 chunk，
   或多租户隔离索引成为硬需求。
4. **TaskQueue → 外部队列**（如 Redis/NATS）：多 worker 水平扩展成为需求
   且 SQLite 租约模型在评测中出现可复现的互斥失败。

任何一项触发时，替换路径是：写新 adapter → 跑
`tests/test_storage_ports.py` 的同一套契约测试 → 通过后经 feature flag
灰度，Local Profile 保持默认。

## 契约测试的语义承诺

`test_storage_ports.py` 同时断言 SQLite 实现（真实现）与内存替身：

- 幂等：同 `(principal_id, idempotency_key)` 重复 submit 返回同 task_id；
- 互斥：claim 后他人 claim 不到同一任务；complete 后永不再 claim；
- 租约：仅持有者能续租；
- 失败可观测：fail 记录原因并递增 attempt_count。

这套断言就是未来 PostgreSQL/外部队列 adapter 的**准入测试**——语义一致
才允许替换，防止"接口对了但行为变了"。

## 后果

- 正面：应用层对队列/存储的依赖收口到 Protocol；替换决策有了
  机器可验证的门槛与书面阈值；ADR-005 的边界声明得到结构化保护。
- 负面/成本：多一层间接（SqliteTaskQueue 与 TaskService 内联 SQL
  目前并存——迁移期状态，见"已知妥协"）。
- 风险：runtime_checkable 的 isinstance 只验结构不验语义——语义由
  契约测试承担，不依赖类型检查。

## 已知妥协（后续 PR 处理，不在本轮）

- TaskService/TaskWorker 内部仍有直接 SQL：本 PR 只建立边界与新路径的
  adapter，**不强行重写已验证的业务面**（861 项回归的稳定优先）。
  收敛方式：新代码走 Protocol，既有路径逐步迁移，迁完删内联 SQL。
- DocumentStore/MetadataStore 的 adapter 未建实体类：边界已定义，
  Local 实现是"现状即满足"（文件系统/SQLite 本身），等真实替换需求
  出现时再包 adapter，避免为抽象而抽象。

## 参考

- ADR-005（单节点硬化——非目标清单的对齐来源）
- `docs/UPGRADE_PLAN.md`（"不引入新向量数据库作为路线图目标"）
- `domain/storage_ports.py` / `infrastructure/sqlite_task_queue.py`
