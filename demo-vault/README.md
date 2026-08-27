---
mindgraph_id: 86d3cfbfc1124d15ba2ab21306c61a02
policy_key: demo.readme
owner: MindGraph Demo
version: "1.0"
status: active
effective_from: 2026-08-01
tags: [说明]
ai_access_level: excluded
---

# MindGraph 企业制度演示库

这是可公开分发的合成数据集，用于在没有私人 Obsidian Vault、真实员工数据或外部模型密钥时复现 MindGraph 的同步、索引和关系扩展链路。

## 目录约定

- `policies/`：当前或历史制度原文；文件名使用 `领域-主题-v版本.md`。
- `workflows/`：审批、例外与执行流程；文件名使用 `流程名.md`。
- `cases/`：完全虚构的边界案例；文件名使用 `case-编号-简述.md`。
- 文档 frontmatter 必须包含 `policy_key`、`owner`、`version`、`status`、`effective_from` 与 `tags`；同一制度的不同版本共用稳定 `policy_key`。
- `status` 仅允许 `active`、`draft`、`archived`；历史版本不得删除，以便测试版本与冲突治理。
- 文档之间使用标准 Markdown 链接，避免依赖某个笔记软件的私有语法。

## 数据边界

所有公司、员工、金额和案例均为合成内容，不代表真实企业政策，也不得直接用于真实报销审批。临时索引应写入系统临时目录或 `data/demo/`；调试完成后可安全重建，不应提交生成的 SQLite/FAISS 文件。
