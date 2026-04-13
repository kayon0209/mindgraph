"""评测模块：30 题标准测试集 + 4 题对抗性测试，混合评分（关键词匹配 + LLM 幻觉检测）。

用法：
    python -m evaluation.runner                  # 运行全部 34 题
    python -m evaluation.runner --no-llm-check   # 跳过 LLM 幻觉检测
    python -m evaluation.runner --standard-only  # 只跑 30 题标准集
    python -m evaluation.runner --adversarial-only  # 只跑 4 题对抗测试
    python -m evaluation.runner --cases 1-10     # 指定题号范围
"""
