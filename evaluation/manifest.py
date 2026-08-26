"""Helpers for reproducible, auditable evaluation manifests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "mindgraph-eval-manifest-v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit(root: str | Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_manifest(
    *,
    root: str | Path,
    suite: str,
    dataset: str | Path,
    dataset_version: str | None,
    configuration: dict[str, Any] | None = None,
    mode: str = "OFFLINE",
    evaluator_version: str | None = None,
    index: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    dataset_path = Path(dataset).resolve()
    try:
        dataset_relpath = str(dataset_path.relative_to(root_path))
    except ValueError:
        dataset_relpath = str(dataset_path)
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "suite": suite,
        "mode": mode,
        "evaluator_version": evaluator_version,
        "git_commit": git_commit(root_path),
        "dataset": {
            "path": dataset_relpath,
            "sha256": sha256_file(dataset_path),
            "version": dataset_version,
        },
        "index": index or {"version": None, "sha256": None},
        "model": model or {"embedder": None, "reranker": None, "llm": None},
        "configuration": configuration or {},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }


def to_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
