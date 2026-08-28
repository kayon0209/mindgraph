import type { Citation } from "../types";

/**
 * 版本时效判定（前端纯展示层）：
 * 后端 Citation 载荷已携带 policy_status / effective_from / effective_to
 * （src/domain/models.py Citation；chat_service 从 chunk metadata 填充），
 * 此处只做「查询时点该版本是否现行有效」的推导，不新增任何后端依赖。
 *
 * policy_status 合法值来自 vault_sync_service.POLICY_STATUSES：
 * draft / active / expired / superseded / archived（+ 兜底 unspecified）。
 */

export type ValidityLevel = "current" | "caution" | "stale";

export type CitationValidity = {
  level: ValidityLevel;
  /** 短标签，用于证据卡角标 */
  label: string;
  /** 一句话解释，用于悬浮提示与警告横幅 */
  detail: string;
};

const STATUS_LABELS: Record<string, string> = {
  active: "现行有效",
  draft: "草案",
  expired: "已失效",
  superseded: "已被替代",
  archived: "已归档",
  unspecified: "状态未登记",
};

/** 只取 ISO 日期前缀，保证可以按字典序比较；解析失败返回 null */
function parseDay(value: string | null | undefined): string | null {
  if (!value) return null;
  const match = value.match(/(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : null;
}

export function todayIso(): string {
  const now = new Date();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function statusLabel(status: string | null | undefined): string {
  return STATUS_LABELS[status || "unspecified"] || status || "状态未登记";
}

/**
 * 判定一条引用在查询时点（asOf，缺省为今天）的时效。
 * stale = 不应作为执行依据（已失效/已被替代/已归档/有效期早于查询日）；
 * caution = 需要人工留意（草案、尚未生效、状态未登记）；
 * current = 登记为现行有效。
 */
export function citationValidity(citation: Citation, asOf?: string | null): CitationValidity {
  const asOfDay = parseDay(asOf) ?? todayIso();
  const status = citation.policy_status || "unspecified";
  const from = parseDay(citation.effective_from);
  const to = parseDay(citation.effective_to);

  if (status === "expired" || status === "superseded" || status === "archived") {
    return {
      level: "stale",
      label: STATUS_LABELS[status],
      detail: `登记状态为「${STATUS_LABELS[status]}」，该版本不是现行制度，采纳前请核实现行版本。`,
    };
  }
  if (to && to < asOfDay) {
    return {
      level: "stale",
      label: "有效期已截止",
      detail: `有效期截至 ${to}，早于查询日期 ${asOfDay}：该版本在查询时点已失效。`,
    };
  }
  if (status === "draft") {
    return {
      level: "caution",
      label: "草案",
      detail: "该制度仍为草案、尚未发布，结论不能作为执行依据。",
    };
  }
  if (from && from > asOfDay) {
    return {
      level: "caution",
      label: "尚未生效",
      detail: `生效日期为 ${from}，晚于查询日期 ${asOfDay}，查询时点该版本尚未生效。`,
    };
  }
  if (status === "active") {
    return {
      level: "current",
      label: "现行有效",
      detail: citation.effective_from
        ? `登记为现行有效（${citation.effective_from} 起${citation.effective_to ? `，至 ${citation.effective_to}` : ""}）。`
        : "登记为现行有效。",
    };
  }
  return {
    level: "caution",
    label: "状态未登记",
    detail: "该制度未登记生效状态与有效期，建议联系制度责任人确认后再采纳。",
  };
}

/** 按轮次聚合：哪些引用需要警示（stale 优先，其次 caution） */
export function summarizeCitationValidity(
  citations: Citation[],
  asOf?: string | null,
): { stale: { citation: Citation; validity: CitationValidity }[]; caution: { citation: Citation; validity: CitationValidity }[] } {
  const stale: { citation: Citation; validity: CitationValidity }[] = [];
  const caution: { citation: Citation; validity: CitationValidity }[] = [];
  for (const citation of citations) {
    const validity = citationValidity(citation, asOf);
    if (validity.level === "stale") stale.push({ citation, validity });
    else if (validity.level === "caution") caution.push({ citation, validity });
  }
  return { stale, caution };
}
