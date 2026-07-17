from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from infrastructure.openai_compatible_provider import OpenAICompatibleProvider


def main() -> None:
    load_dotenv(ROOT / ".env")
    provider = OpenAICompatibleProvider(
        os.getenv("OPENAI_COMPAT_PROVIDER_NAME", "openai_compatible"),
        os.getenv("OPENAI_COMPAT_BASE_URL", ""),
        os.getenv("OPENAI_COMPAT_API_KEY", ""),
        os.getenv("OPENAI_COMPAT_MODEL", ""),
        float(os.getenv("CHAT_TIMEOUT_SECONDS", "60")),
        int(os.getenv("CHAT_MAX_RETRIES", "1")),
        os.getenv("OPENAI_COMPAT_VERIFIED", "false").lower() == "true",
    )
    messages = [
        {"role": "system", "content": "仅根据证据回答，不得扩写。"},
        {"role": "user", "content": "证据：差旅报销应提交有效票据。问题：差旅报销应提交什么？"},
    ]
    health = provider.health_check()
    started = time.perf_counter()
    answer, usage = provider.complete(messages)
    non_stream_latency = round((time.perf_counter() - started) * 1000, 3)
    started = time.perf_counter()
    first_token = None
    streamed = []
    stream_usage = {"usage_source": "unavailable"}
    for event in provider.stream(messages):
        if event.get("delta"):
            if first_token is None:
                first_token = round((time.perf_counter() - started) * 1000, 3)
            streamed.append(event["delta"])
        if event.get("usage"):
            stream_usage = event["usage"]
    stream_latency = round((time.perf_counter() - started) * 1000, 3)
    streamed_answer = "".join(streamed)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_type": "single_provider_transport_verification",
        "provider": health,
        "non_streaming": {
            "passed": bool(answer), "latency_ms": non_stream_latency, "answer_length": len(answer),
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(), "usage": usage,
        },
        "streaming": {
            "passed": bool(streamed_answer), "ttft_ms": first_token, "total_latency_ms": stream_latency,
            "answer_length": len(streamed_answer), "answer_sha256": hashlib.sha256(streamed_answer.encode()).hexdigest(),
            "usage": stream_usage,
        },
        "cost": {"value": None, "source": "unavailable", "currency": None, "pricing_version": None},
        "limitations": [
            "Only the configured DeepSeek-compatible provider was verified with real credentials.",
            "This is not a multi-model quality comparison and contains no answer-correctness claim.",
            "Zhipu is not configured; Anthropic is tested with mocks only.",
        ],
    }
    output = ROOT / "evaluation" / "results" / "providers"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"provider_verification_{stamp}.json"
    csv_path = output / f"provider_verification_{stamp}.csv"
    markdown_path = ROOT / "docs" / "evaluation" / "provider-verification.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    row = {
        "provider": health["provider"], "model": health["model"], "health_status": health["health_status"],
        "non_streaming_passed": result["non_streaming"]["passed"], "streaming_passed": result["streaming"]["passed"],
        "non_streaming_latency_ms": non_stream_latency, "ttft_ms": first_token, "streaming_total_latency_ms": stream_latency,
        "usage_source": usage["usage_source"], "estimated_cost": None,
    }
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    markdown_path.write_text("\n".join([
        "# Provider verification", "",
        "This is a single-provider transport verification, not a model quality comparison.", "",
        "| Provider | Model | Health | Non-stream | Stream | TTFT (ms) | Total stream latency (ms) | Usage source | Cost |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
        f"| {row['provider']} | {row['model']} | {row['health_status']} | {row['non_streaming_passed']} | {row['streaming_passed']} | {row['ttft_ms']} | {row['streaming_total_latency_ms']} | {row['usage_source']} | unavailable |",
        "", "Zhipu is not configured. Anthropic is implemented and mock-tested but not verified with real credentials.",
    ]), encoding="utf-8")
    print(json_path)
    print(csv_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
