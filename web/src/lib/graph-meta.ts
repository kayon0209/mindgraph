/** 图谱可视化与关系展示的共享元数据：类型中文标签、边颜色、节点分类颜色。
 *  GraphPage / RelationsPage / KnowledgePage 抽屉共用，保证全站口径一致。 */

export const RELATION_TYPE_LABELS: Record<string, string> = {
  SUPERSEDES: "版本替代",
  CONTRADICTS: "内容冲突",
  REQUIRES_APPROVAL: "需审批",
  APPLIES_TO: "适用范围",
  REFERENCES: "引用",
  EXTENDS: "扩展",
  CO_ASKED: "共同提问",
  RELATED: "相关",
};

export function relationTypeLabel(type: string): string {
  return RELATION_TYPE_LABELS[type] ?? type;
}

/** 边颜色：治理语义强的类型用强调色，其余用中性线色 */
export const RELATION_TYPE_COLORS: Record<string, string> = {
  SUPERSEDES: "var(--danger)",
  CONTRADICTS: "var(--amber)",
  REQUIRES_APPROVAL: "var(--moss)",
  APPLIES_TO: "var(--accent-dark)",
  CO_ASKED: "var(--ink-soft)",
};

export function relationTypeColor(type: string): string {
  return RELATION_TYPE_COLORS[type] ?? "var(--line-strong)";
}

/** 节点分类颜色：category 即 vault 目录名（policies/workflows/cases/external/根目录） */
export const CATEGORY_COLORS: Record<string, string> = {
  policies: "var(--accent-dark)",
  workflows: "var(--moss)",
  cases: "var(--amber)",
  external: "#7a7f74",
};

export const CATEGORY_LABELS: Record<string, string> = {
  policies: "制度",
  workflows: "流程",
  cases: "案例",
  external: "外部手册",
  根目录: "根目录",
};

export function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? "var(--line-dark)";
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}
