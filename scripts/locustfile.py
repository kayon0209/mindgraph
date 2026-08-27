"""Locust 负载测试（本地 MindGraph 服务）。

替代 k6（k6 二进制下载被网络策略拦截，改用纯 Python 的 locust，如实标注）。
运行（managed env）：
  locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
         --host http://127.0.0.1:8123 --only-summary
"""
from locust import HttpUser, between, task


class MindGraphUser(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(1)
    def health(self) -> None:
        # 纯 web 层（无 DB/索引访问）
        self.client.get("/api/v1/health")

    @task(3)
    def config_public(self) -> None:
        # 真实 container 链路（读取索引状态 / 配置）
        self.client.get("/api/v1/config/public")
