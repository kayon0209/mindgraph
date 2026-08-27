# 测试目录（`tests/`）

回归与契约测试：覆盖检索、评测数据、Golden 标签与指标计算；在线模型调用不进入单元测试。

## 运行方式

项目测试基于 **pytest**（含 `conftest.py` 提供的共享配置）。全量运行：

```bash
python -m pytest -q --no-cov
```

> 说明：本环境 coverage 清理会被沙箱回收站策略拦截，故 CI/复现命令使用 `--no-cov`；
> 常规环境直接 `python -m pytest -q` 即可（见 `docs/upgrade/EXECUTION_STATUS_2026-08-27.md`）。

## 组织约定

- 文件名使用 `test_<subject>.py` 命名
- 评测数据、Golden 标签与指标计算的回归测试放在本目录
- 需要真实模型/Provider 的用例不进入单元测试（在线评测走 `scripts/run_*_evaluation.py`）

## 相关

- 评测脚本：`scripts/`（`run_ablation.py`、`run_routing_evaluation.py`、`run_answer_evaluation.py`）
- Golden 数据集：`evaluation/datasets/mindgraph_golden_v2.jsonl`
