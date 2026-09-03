"""MCP stdio 冒烟测试（M1，实施方案补丁 A）。

不依赖任何 MCP 客户端：直接对 ``src/mcp_server.py`` 的 stdio JSON-RPC
接口执行 C1–C8 用例（见 docs/MCP-CLIENT-COMPATIBILITY.md），输出机器可读
结果（JSONL，每行 {case, status, detail}）。三个目标客户端（Claude Code /
Cursor / OpenHands）的手工接入步骤只验证连接、发现与渲染，由该文档承载。

主体语义（重要）：``MCP_PRINCIPAL`` 只注入名字（authenticated=True、无
角色/无 allow）。该 scope 对私有内容不可见——C3 主路径要求命中带治理
元数据的 citations，因此 smoke 默认在子进程 env 里附加
``MCP_SMOKE_ADMIN=1`` 无效；实际通过 ``--principal-roles admin`` 把角色写进
``MCP_PRINCIPAL_ROLES``（mcp_server 支持 roles 读取）。受限主体（C4）用
默认无角色主体验证 public-only 裁剪。

用法（本地，需已同步 demo vault 索引或任意可用运行库）：
    .venv/Scripts/python.exe scripts/mcp_stdio_smoke.py
    .venv/Scripts/python.exe scripts/mcp_stdio_smoke.py --json   # 纯 JSONL 输出

退出码：C1–C7 任一 FAIL 即非零（可接入 CI）；C8 仅记录不阻断。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

DEFAULT_QUERY = "差旅费报销的时限是多少天"


class StdioMcpClient:
    """以子进程方式拉起 mcp_server.py 并行 JSON-RPC 通信（行分隔）。"""

    def __init__(self, principal_name: str | None = "smoke_agent", roles: str | None = None) -> None:
        env = os.environ.copy()
        # mcp_server 经 api.dependencies → evaluation.baseline 间接导入 evaluation/，
        # stdio 子进程需同时可见 src/ 与仓库根（与 CI dataset-contract 步骤同理）
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + str(PROJECT_ROOT)
        if principal_name:
            env["MCP_PRINCIPAL"] = principal_name
        if roles:
            # 无角色主体的 allow/deny 均空 → 私有内容不可见（受限 scope）；
            # admin 角色获得 "*" 全量可见（C3 主路径需要）
            env["MCP_PRINCIPAL_ROLES"] = roles
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._cases: list[dict] = []

    def __enter__(self) -> "StdioMcpClient":
        self._proc = subprocess.Popen(
            [PYTHON, "-u", str(PROJECT_ROOT / "src" / "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=self._env,
        )
        return self

    def __exit__(self, *_exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.terminate()
        self._proc = None

    def request(self, method: str, params: dict | None = None, msg_id: object = 1) -> dict:
        assert self._proc and self._proc.stdin and self._proc.stdout
        message: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("mcp_server closed stdout before responding")
        return json.loads(line)

    def tool_call(self, name: str, arguments: dict, msg_id: object = 1) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments}, msg_id=msg_id)

    @staticmethod
    def tool_body(response: dict) -> dict:
        return json.loads(response["result"]["content"][0]["text"])


def run_cases(principal_all: bool = True) -> list[dict]:
    results: list[dict] = []

    def record(case: str, status: str, detail: str = "") -> None:
        results.append({"case": case, "status": status, "detail": detail})
        if not JSON_ONLY:
            print(f"[{status:>7}] {case}: {detail}", file=sys.stderr)

    # 主会话用 admin 角色（C3 需要命中私有差旅制度）；C4 在同会话内
    # 用受限主体语义验证（tools/call 直呼走 mcp_server 的 principal 即本会话主体，
    # 受限验证由 --restricted 切换为无角色主体单独跑一遍 C4）
    roles = "admin" if principal_all and not RESTRICTED else None
    with StdioMcpClient(principal_name="smoke_agent", roles=roles) as client:
        # C1 握手
        try:
            r = client.request("initialize", {}, msg_id="c1")
            info = r["result"]["serverInfo"]
            assert r["result"]["protocolVersion"] == "2024-11-05"
            assert info["name"] == "mindgraph-mcp"
            record("C1", "PASS", f"protocol 2024-11-05, server {info['name']} {info['version']}")
        except Exception as exc:
            record("C1", "FAIL", str(exc))

        # C2 发现：8 个工具 + 合法 JSON Schema
        try:
            r = client.request("tools/list", msg_id="c2")
            tools = r["result"]["tools"]
            names = {t["name"] for t in tools}
            expected = {
                "mindgraph_list_notes", "mindgraph_get_note", "mindgraph_search",
                "mindgraph_evaluation_overview", "mindgraph_list_relations",
                "mindgraph_get_policy_history", "mindgraph_concept_gaps", "mindgraph_verify_citations",
            }
            assert expected <= names, f"missing: {expected - names}"
            for t in tools:
                assert t["inputSchema"]["type"] == "object"
            record("C2", "PASS", f"{len(tools)} tools listed (expected 8 present)")
        except Exception as exc:
            record("C2", "FAIL", str(exc))

        # C3 检索主路径：citations 带版本/生效期/policy_key 元数据
        try:
            r = client.tool_call("mindgraph_search", {"query": DEFAULT_QUERY, "top_k": 5, "strategy": "hybrid"}, msg_id="c3")
            body = StdioMcpClient.tool_body(r)
            citations = body["citations"]
            assert citations, "no citations returned"
            meta_keys = {"document_version", "effective_from", "effective_to", "policy_status", "policy_key"}
            first = citations[0]
            assert meta_keys <= set(first), f"citation missing governance metadata: {meta_keys - set(first)}"
            record("C3", "PASS", f"{len(citations)} citations with governance metadata")
        except Exception as exc:
            record("C3", "FAIL", str(exc))

        # C4 ACL：受限主体（无角色）只见公开笔记；有权限缺口时 get_note 返回
        # not_found（不泄漏存在性）。断言收紧：公开项必须可见——0 可见即 FAIL
        # （防"公开内容被错误隐藏"的假阳性），且无私有制度泄漏。
        try:
            if RESTRICTED:
                r = client.tool_call("mindgraph_list_notes", {"limit": 50}, msg_id="c4a")
                body = StdioMcpClient.tool_body(r)
                titles = [item["title"] for item in body["items"]]
                if not titles:
                    record("C4", "FAIL", "restricted principal sees 0 notes - public content must stay visible (check acl_public data)")
                elif any(("差旅" in str(t)) or ("报销" in str(t)) for t in titles):
                    record("C4", "FAIL", f"restricted principal sees private notes: {[t for t in titles if '差旅' in str(t) or '报销' in str(t)]}")
                else:
                    r2 = client.tool_call("mindgraph_get_note", {"note_id": "nonexistent"}, msg_id="c4b")
                    body2 = StdioMcpClient.tool_body(r2)
                    assert body2 == {"error": "note not found"}, body2
                    record("C4", "PASS", f"restricted principal sees {len(titles)} public notes; missing note → not_found")
            else:
                record("C4", "SKIP", "run with --restricted to exercise ACL case")
        except Exception as exc:
            record("C4", "FAIL", str(exc))

        # C5 错误：非法参数 → -32602；未知方法 → -32601
        try:
            r = client.tool_call("mindgraph_get_policy_history", {"policy_key": ""}, msg_id="c5a")
            assert r["error"]["code"] == -32602, f"expected -32602, got {r.get('error')}"
            r = client.request("tools/nope", msg_id="c5b")
            assert r["error"]["code"] == -32601
            record("C5", "PASS", "-32602 invalid params / -32601 unknown method")
        except Exception as exc:
            record("C5", "FAIL", str(exc))

        # C6 超时：deadline 语义由服务端协作式实现；stdio 通道无外部 deadline 注入，
        # 以“未知工具错误 + 服务不挂起”作为通道可用性下界
        try:
            r = client.tool_call("mindgraph_nonexistent", {}, msg_id="c6")
            assert "error" in r and r["error"]["code"] in {-32602, -32603}
            alive = client.request("tools/list", msg_id="c6b")  # 服务仍响应
            assert "result" in alive
            record("C6", "PASS", "server responsive after error path (stdio deadline needs client-side review)")
        except Exception as exc:
            record("C6", "FAIL", str(exc))

        # C7 契约：verify_citations 返回机器可判定字段
        try:
            r = client.tool_call(
                "mindgraph_verify_citations",
                {"answer": "依据 [citation-1]", "citation_ids": ["citation-1"]},
                msg_id="c7",
            )
            body = StdioMcpClient.tool_body(r)
            assert {"passed", "applicable", "checks"} <= set(body)
            record("C7", "PASS", "citation integrity verdict machine-readable")
        except Exception as exc:
            record("C7", "FAIL", str(exc))

        # C8 性能：search P95（单样本计时，先不阻断）
        try:
            samples = []
            for i in range(5):
                started = time.perf_counter()
                client.tool_call("mindgraph_search", {"query": DEFAULT_QUERY, "top_k": 3}, msg_id=f"c8-{i}")
                samples.append((time.perf_counter() - started) * 1000)
            p95 = sorted(samples)[int(0.95 * len(samples)) - 1]
            record("C8", "PASS", f"search latency ~p95 {p95:.0f} ms (5 samples, informational)")
        except Exception as exc:
            record("C8", "FAIL", str(exc))

    return results


JSON_ONLY = False
RESTRICTED = False


def main() -> int:
    global JSON_ONLY, RESTRICTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="only emit JSONL results to stdout")
    parser.add_argument("--restricted", action="store_true", help="run C4 with a restricted (public_only) principal")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    args = parser.parse_args()
    JSON_ONLY, RESTRICTED = args.json, args.restricted

    results = run_cases()
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    blocking = [item for item in results if item["case"] not in {"C8"} and item["status"] == "FAIL"]
    if not JSON_ONLY:
        print(
            f"\nsummary: {sum(1 for i in results if i['status'] == 'PASS')} PASS / "
            f"{sum(1 for i in results if i['status'] == 'FAIL')} FAIL / {sum(1 for i in results if i['status'] == 'SKIP')} SKIP",
            file=sys.stderr,
        )
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
