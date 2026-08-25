import type { GovernanceCase } from "../types";

export type GovernanceAction = "confirm" | "reject" | "revoke";

const REASON_LABELS: Record<string, string> = {
  overlapping_effective_intervals: "有效期重叠",
  same_version_different_checksum: "同版本内容不一致",
  exact_duplicate: "内容完全重复",
  confirmed_duplicate_alias: "已确认为重复别名",
  confirmed_conflict_block: "已确认冲突阻断",
};

export function governanceReasonLabel(reasonCode: string): string {
  return REASON_LABELS[reasonCode] || "需要人工核验";
}

export function governanceCaseView(value: GovernanceCase): {
  reasonLabel: string;
  actions: GovernanceAction[];
} {
  const actions: GovernanceAction[] = [];
  if (value.capabilities.can_resolve) actions.push("confirm", "reject");
  if (value.capabilities.can_revoke) actions.push("revoke");
  return { reasonLabel: governanceReasonLabel(value.reason_code), actions };
}
