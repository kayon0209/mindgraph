import type { EvaluationRun } from "../types";
import { METRIC_DEFINITIONS, metricValue } from "../lib/metrics";

const STRATEGY_LABELS: Record<string, string> = {
  bm25: "BM25",
  bm25_vector: "Hybrid",
  bm25_vector_graph: "Hybrid + 关系",
  dense: "Dense",
  hybrid: "Hybrid",
  hybrid_rerank: "Hybrid + Rerank",
};

export function BarComparison({ runs }: { runs: EvaluationRun[] }) {
  return (
    <div className="bar-comparison" aria-label="检索策略指标对比">
      {METRIC_DEFINITIONS.map((metric) => (
        <section className="bar-group" key={metric.key}>
          <div className="bar-group-heading">
            <strong>{metric.label}</strong>
            <span>0—100%</span>
          </div>
          <div className="bar-rows">
            {runs.map((run) => {
              const value = metricValue(run, metric.key);
              return (
                <div className="bar-row" key={`${run.run_id}-${metric.key}`}>
                  <span className="bar-label">{STRATEGY_LABELS[run.strategy] || run.strategy}</span>
                  <div className="bar-track">
                    <span className="bar-fill" style={{ width: `${(value ?? 0) * 100}%` }} />
                  </div>
                  <span className="bar-value">{value === null ? "—" : `${(value * 100).toFixed(1)}%`}</span>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
