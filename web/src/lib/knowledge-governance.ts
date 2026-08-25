import type { GovernanceCase } from "../types";

export type GovernanceAction = "confirm" | "reject" | "revoke";

const REASON_LABELS: Record<string, string> = {
  overlapping_effective_intervals: "有效期重叠",
  same_version_different_checksum: "同版本内容不一致",
  exact_duplicate_equivalent: "内容完全重复",
  checksum_match_requires_review: "校验和一致，需人工核验",
};

export function governanceReasonLabel(reasonCode: string): string {
  return REASON_LABELS[reasonCode] || "需要人工核验";
}

export function governanceFailureView(error: unknown): { refresh: boolean; message: string } {
  const value = error as { status?: number; message?: string };
  if (value?.status === 409) {
    return { refresh: true, message: "事项状态已变化，列表已刷新；请基于最新状态重试。" };
  }
  const message = value?.message || "治理操作失败";
  return { refresh: false, message: `${message}；当前列表已保留，可重试。` };
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
