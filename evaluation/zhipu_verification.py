from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import ZHIPU_API_KEY, ZHIPU_MODEL
from infrastructure.chat_provider import ZhipuChatProvider


def main() -> None:
    load_dotenv(ROOT / ".env")
    provider = ZhipuChatProvider(ZHIPU_API_KEY, ZHIPU_MODEL)
    messages = [{"role": "user", "content": "只回答：测试"}]
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
    stream_answer = "".join(streamed)
    stream_latency = round((time.perf_counter() - started) * 1000, 3)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_type": "single_provider_transport_verification",
        "provider": "zhipu",
        "model": ZHIPU_MODEL,
        "configured": provider.available,
        "verified": bool(answer and stream_answer),
        "non_streaming": {
            "passed": bool(answer), "latency_ms": non_stream_latency,
            "answer_length": len(answer), "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "usage": usage,
        },
        "streaming": {
            "passed": bool(stream_answer), "ttft_ms": first_token, "total_latency_ms": stream_latency,
            "answer_length": len(stream_answer), "answer_sha256": hashlib.sha256(stream_answer.encode()).hexdigest(),
            "usage": stream_usage,
        },
        "cost": {"value": None, "source": "unavailable", "currency": None, "pricing_version": None},
        "limitations": [
            "Transport/model-access verification only; no answer-quality comparison is claimed.",
            "Pricing metadata is unavailable, so cost is null.",
        ],
    }
    output = ROOT / "evaluation" / "results" / "providers"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"zhipu_verification_{stamp}.json"
    csv_path = output / f"zhipu_verification_{stamp}.csv"
    markdown_path = ROOT / "docs" / "evaluation" / "zhipu-verification.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    row = {
        "provider": "zhipu", "model": ZHIPU_MODEL, "verified": result["verified"],
        "non_streaming_passed": result["non_streaming"]["passed"],
        "streaming_passed": result["streaming"]["passed"],
        "non_streaming_latency_ms": non_stream_latency, "ttft_ms": first_token,
        "streaming_total_latency_ms": stream_latency, "usage_source": usage["usage_source"],
        "estimated_cost": None,
    }
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    markdown_path.write_text("\n".join([
        "# Zhipu provider verification", "",
        "This verifies model access and transport only; it is not an answer-quality comparison.", "",
        "| Provider | Model | Verified | Non-stream | Stream | TTFT (ms) | Stream latency (ms) | Usage source | Cost |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
        f"| zhipu | {ZHIPU_MODEL} | {row['verified']} | {row['non_streaming_passed']} | {row['streaming_passed']} | {row['ttft_ms']} | {row['streaming_total_latency_ms']} | {row['usage_source']} | unavailable |",
    ]), encoding="utf-8")
    print(json_path)
    print(csv_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
