# 检索包约定（`src/retrieval/`）

混合检索管线：从索引构建到多策略检索、融合与重排的完整实现。

## 模块划分

- `types.py`：共享协议、候选、trace 与配置无关的数据契约
- `embeddings.py` / `dense.py` / `sparse.py` / `fusion.py` / `reranker.py`：各负责检索管线的一个阶段
- `pipeline.py`：编排各阶段；检索策略不得重复实现完整管线
- `indexing.py`：版本化全量索引重建与语料快照
- `mindgraph_pipeline.py`：包装管线，附加受控图谱一跳扩展与多查询变体 RRF 融合

## 必须遵守的边界

- 稳定 chunk ID 使用 `<doc_name>::<chunk_index>` 格式，且必须与冻结评测标签保持一致
- 正式评测**不得**静默使用 Hash Embedding（假向量）
- 跨语言查询变体按排名（RRF）融合，而非直接比较分数（见 `mindgraph_pipeline.py` 的 `query_variants`）

## 相关

- 评测与 Golden 数据集：`evaluation/`
- 成本与效率分析：`docs/MindGraph-cost-efficiency.md`
