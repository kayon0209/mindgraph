/**
 * P1-A3：质量趋势 sparkline 纯函数测试。
 */

import { describe, expect, it } from "vitest";

import { sparkNormalize, sparkPath, sparkSeries } from "./quality-spark";

const RUNS = [
  { status: "completed", dataset: "run-3", metrics: { citation_fidelity: 1.0, p95_total_latency_ms: 120 } },
  { status: "completed", dataset: "run-2", metrics: { citation_fidelity: 0.9, p95_total_latency_ms: 90 } },
  { status: "failed", dataset: "run-x", metrics: { citation_fidelity: 0.1 } }, // 失败轮剔除
  { status: "completed", dataset: "run-1", metrics: { citation_fidelity: 0.8, p95_total_latency_ms: 150 } },
];

describe("quality spark（P1-A3）", () => {
  it("趋势序列：按时间正序、剔除失败轮、缺指标轮跳过", () => {
    const series = sparkSeries(RUNS, "citation_fidelity");
    expect(series.map((p) => p.value)).toEqual([0.8, 0.9, 1.0]);
    expect(series.map((p) => p.dataset)).toEqual(["run-1", "run-2", "run-3"]);
  });

  it("率类指标归一：越大越好，max → 1", () => {
    const norm = sparkNormalize(sparkSeries(RUNS, "citation_fidelity"), true);
    expect(norm[2]).toBe(1);
    expect(norm[0]).toBeCloseTo(0.8, 5);
  });

  it("延迟类指标归一：越低越好，最小延迟 → 1", () => {
    const norm = sparkNormalize(sparkSeries(RUNS, "p95_total_latency_ms"), false);
    // run-2 的 90ms 最快 → 归 1
    expect(norm[1]).toBe(1);
    expect(norm[0]).toBeLessThan(norm[1]);
  });

  it("单点序列不可画线（path 为空，卡片显示数值而非折线）", () => {
    expect(sparkPath([1])).toBe("");
    expect(sparkPath([0.2, 0.8, 1])).toContain(",");
  });

  it("恒定值归一为全 1（不制造假波动）", () => {
    const norm = sparkNormalize([{ runIndex: 0, dataset: "a", value: 5 }, { runIndex: 1, dataset: "b", value: 5 }], true);
    expect(norm).toEqual([1, 1]);
  });
});
