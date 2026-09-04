/**
 * P1-A3：跨轮质量趋势 sparkline（纯 SVG，Mono 视觉语法——炭墨折线 + 纸灰底 +
 * 细网格；lieflat-charts F2 Hairline Line 的 React 化）。
 *
 * 数据：衡量页已加载的最近 20 次 evaluation_runs（按时间正序取含该指标的轮）。
 * 语义：每轮一点，y 轴按 0–1 指标归一（延迟类指标反序——越低越好）。
 */

export type SparkSeries = {
  key: string;
  label: string;
  /** 数值越大越好（指标率）；false = 越低越好（延迟类） */
  higherIsBetter: boolean;
};

export const SPARK_METRICS: SparkSeries[] = [
  { key: "citation_correctness", label: "引用正确性", higherIsBetter: true },
  { key: "citation_fidelity", label: "引用保真", higherIsBetter: true },
  { key: "version_validity", label: "版本有效性", higherIsBetter: true },
  { key: "refusal_correctness", label: "拒答正确性", higherIsBetter: true },
  { key: "p95_total_latency_ms", label: "P95 延迟", higherIsBetter: false },
];

export type SparkPoint = { runIndex: number; dataset: string; value: number };

/** 从 runs 抽一条趋势序列（按时间正序、仅 completed、含该指标的轮）。 */
export function sparkSeries(
  runs: Array<{ status: string; metrics: Record<string, number | string | null | undefined>; dataset: string }>,
  key: string,
): SparkPoint[] {
  const points: SparkPoint[] = [];
  [...runs]
    .filter((run) => run.status === "completed")
    .reverse()
    .forEach((run, index) => {
      const raw = run.metrics[key];
      const value = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
      if (value !== null) points.push({ runIndex: index, dataset: run.dataset, value });
    });
  return points;
}

/** 归一化到 0–1（率类直接用值；延迟类 0–max 区间反序）。 */
export function sparkNormalize(points: SparkPoint[], higherIsBetter: boolean): number[] {
  if (!points.length) return [];
  const values = points.map((p) => p.value);
  const max = Math.max(...values);
  const min = Math.min(...values);
  return values.map((v) => {
    if (higherIsBetter) return max === min ? 1 : v / (max || 1);
    // 越低越好：min 归 1，max 归 0
    return max === min ? 1 : 1 - (v - min) / ((max - min) || 1);
  });
}

/** 生成 SVG polyline 的 points 属性（viewBox 100×28）。 */
export function sparkPath(norm: number[]): string {
  if (norm.length < 2) return "";
  const step = 100 / (norm.length - 1);
  return norm
    .map((v, i) => `${(i * step).toFixed(1)},${(26 - v * 22).toFixed(1)}`)
    .join(" ");
}
