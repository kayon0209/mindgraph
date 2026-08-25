"""MindGraph 增量同步看门狗（M1-D3）。

环境无 watchdog 依赖，采用轻量轮询：
- 周期扫描 Vault（检测新增 / 修改 / 删除 / 重命名 / 移动）→ 标记 pending；
- 若有待索引笔记，触发 MindGraphIndexService.build()（带去抖动，等编辑稳定）。
"""
from __future__ import annotations

from datetime import date
import logging
import time
from typing import Any

from .governance_reconciliation_service import GovernanceReconciliationService
from .mindgraph_index_service import MindGraphIndexService
from .vault_sync_service import VaultSyncService

logger = logging.getLogger("mindgraph.sync")


class MindGraphSyncWatcher:
    def __init__(
        self,
        scan_service: VaultSyncService,
        index_service: MindGraphIndexService,
        poll_interval: float = 3.0,
        debounce: float = 0.6,
        governance_reconciler: GovernanceReconciliationService | None = None,
    ) -> None:
        self.scan = scan_service
        self.index = index_service
        self.poll_interval = poll_interval
        self.debounce = debounce
        self.governance_reconciler = governance_reconciler
        self._running = False

    def run_once(self) -> dict[str, Any]:
        scan = self.scan.scan_vault()
        result: dict[str, Any] = {
            "scanned": len(scan.scanned),
            "skipped": len(scan.skipped),
            "pruned": scan.pruned,
            "errors": scan.errors,
            "indexed": False,
            "index_version": None,
        }
        if scan.errors:
            result["status"] = "failed"
            return result
        if self.governance_reconciler is not None:
            self.governance_reconciler.reconcile(as_of=date.today())
        # 删除场景：笔记已从 notes 表剪枝，无 pending 可触发，但旧索引仍含其 chunk，
        # 需 force 重建以排除。编辑场景走 pending 触发（force=False，命中 embedding 缓存）。
        if self.index.has_pending() or scan.pruned > 0:
            time.sleep(self.debounce)  # 去抖：连续保存时等编辑稳定再构建
            if self.governance_reconciler is None:
                manifest = self.index.build(force=scan.pruned > 0)
            else:
                manifest = self.index.build(
                    force=scan.pruned > 0, governance_reconciled=True
                )
            result["build"] = manifest
            result["indexed"] = manifest.get("status") != "noop"
            result["index_version"] = manifest.get("index_version")
            result["reused_embeddings"] = manifest.get("reused_embeddings")
            result["new_embeddings"] = manifest.get("new_embeddings")
        return result

    def run_forever(self) -> None:
        self._running = True
        logger.info("mindgraph_sync_watcher_started", extra={"poll_interval": self.poll_interval})
        try:
            while self._running:
                try:
                    summary = self.run_once()
                    if summary["indexed"]:
                        logger.info(
                            "mindgraph_index_built",
                            extra={k: summary[k] for k in ("index_version", "reused_embeddings", "new_embeddings")},
                        )
                except Exception:
                    logger.exception("mindgraph_sync_watcher_cycle_error")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self._running = False
            logger.info("mindgraph_sync_watcher_stopped")

    def stop(self) -> None:
        self._running = False
