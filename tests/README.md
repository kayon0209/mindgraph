# Tests directory

- 文件名使用 `test_<subject>.py`。
- 测试只依赖 Python 标准库，使用 `python -m unittest discover -s tests` 独立运行。
- 评测数据、Gold 标签和指标计算的回归测试放在这里；在线模型调用不进入单元测试。
