import type { AnswerResult, RetrievalTrace } from "../types";

export function completionGenerationState(
  result: Pick<AnswerResult, "result_state" | "degraded">,
): "warning" | "done" {
  return result.degraded || result.result_state === "conflicting_evidence"
    ? "warning"
    : "done";
}

export function policyConflictItems(trace: RetrievalTrace | null) {
  return (trace?.policy_conflicts ?? []).flatMap((conflict) =>
    conflict.versions.map((version) => ({
      key: `${conflict.policy_key}-${version.note_id}`,
      policyKey: conflict.policy_key,
      asOf: conflict.as_of,
      title: version.title,
      version: version.version ?? "未登记",
      period: `${version.effective_from ?? "未设置"} → ${version.effective_to ?? "长期有效"}`,
      owner: version.owner ?? "责任人未登记",
      vaultPath: version.vault_path,
    })),
  );
}
