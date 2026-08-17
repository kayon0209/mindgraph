import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Scale, TimerReset } from "lucide-react";

import { BarComparison } from "../components/BarComparison";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from "../components/Primitives";
import { api } from "../lib/api";
import { latestRunPerStrategy, metricValue } from "../lib/metrics";
import type { EvaluationResponse } from "../types";

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function EvaluationPage() {
  const [data, setData] = useState<EvaluationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.evaluations());
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const comparisonRuns = useMemo(() => latestRunPerStrategy(data?.runs ?? []), [data]);
  const bestRecall = comparisonRuns.reduce<number | null>((best, run) => {
    const value = metricValue(run, "recall_at_5");
    if (value === null) return best;
    return best === null ? value : Math.max(best, value);
  }, null);
  const latest = data?.runs[0] ?? null;

  return (
    <div className="page evaluation-page">
      <PageHeader
        eyebrow="Quality ledger / 03"
        title="质量必须能比较，也必须能追责"
        description="指标来自 evaluation_runs，不用静态占位。这里展示最近一次各策略结果，Golden Set 版本必须随运行记录保存。"
        actions={
          <button className="button secondary" onClick={() => void load()} type="button">
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新账本
          </button>
        }
      />

      {loading ? <LoadingState label="读取评测运行与指标" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {!loading && !error && data ? (
        <>
          <div className="metrics-grid reveal reveal-2">
            <MetricCard label="当前最佳 Recall@5" note="最近各策略运行" value={percent(bestRecall)} />
            <MetricCard label="评测运行" note="最多展示最近 20 条" value={data.runs.length} />
            <MetricCard label="已索引制度" note="真实 CURRENT manifest" value={data.library_stats.indexed_notes} />
            <MetricCard label="待审核关系" note="不会自动进入检索" value={data.library_stats.relations_proposed} />
          </div>

          {comparisonRuns.length ? (
            <section className="comparison-section reveal reveal-3">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Ablation comparison</p>
                  <h2>策略消融对比</h2>
                </div>
                <span>{comparisonRuns.length} strategies</span>
              </div>
              <BarComparison runs={comparisonRuns} />
            </section>
          ) : (
            <EmptyState
              title="还没有可渲染的评测指标"
              detail="先运行 scripts/run_ablation.py；页面不会用 Mock 数值替代真实结果。"
            />
          )}

          <section className="runs-section reveal reveal-4">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Run history</p>
                <h2>运行记录</h2>
              </div>
              {latest ? <span>latest · {latest.strategy}</span> : null}
            </div>
            {data.runs.length ? (
              <div className="run-table-wrap">
                <table className="run-table">
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th>数据集</th>
                      <th>Recall@5</th>
                      <th>MRR</th>
                      <th>平均延迟</th>
                      <th>状态</th>
                      <th>完成时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.runs.map((run) => (
                      <tr key={run.run_id}>
                        <td><strong>{run.strategy}</strong></td>
                        <td>{run.dataset}</td>
                        <td>{percent(metricValue(run, "recall_at_5"))}</td>
                        <td>{percent(metricValue(run, "mrr"))}</td>
                        <td>{typeof run.metrics.mean_retrieval_latency_ms === "number" ? `${run.metrics.mean_retrieval_latency_ms.toFixed(0)} ms` : "—"}</td>
                        <td><StatusPill value={run.status} /></td>
                        <td>{run.finished_at ? new Date(run.finished_at).toLocaleString("zh-CN") : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>

          <div className="evaluation-notes reveal reveal-4">
            <div><Scale size={18} /><p><strong>不能自证</strong><span>Golden 标签不得从 confirmed 关系或运行排序反向生成。</span></p></div>
            <div><TimerReset size={18} /><p><strong>成本同屏</strong><span>后续将 token、P95 延迟与质量指标放在同一版本账本。</span></p></div>
          </div>
        </>
      ) : null}
    </div>
  );
}
