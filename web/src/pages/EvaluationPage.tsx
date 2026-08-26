import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Scale, TimerReset } from "lucide-react";

import { BarComparison } from "../components/BarComparison";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from "../components/Primitives";
import { api } from "../lib/api";
import { evaluationEfficiencyView, latestRunsForMetric, latestRunWithMetric, metricValue, numericMetricValue } from "../lib/metrics";
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

  const comparisonRuns = useMemo(() => latestRunsForMetric(data?.runs ?? [], "recall_at_5"), [data]);
  const bestRecall = comparisonRuns.reduce<number | null>((best, run) => {
    const value = metricValue(run, "recall_at_5");
    if (value === null) return best;
    return best === null ? value : Math.max(best, value);
  }, null);
  const latest = data?.runs[0] ?? null;
  const latestAnswerRun = latestRunWithMetric(data?.runs ?? [], "citation_correctness");
  const latestEfficiencyRun = latestRunWithMetric(data?.runs ?? [], "p95_total_latency_ms");
  const latestRoutingRun = latestRunWithMetric(data?.runs ?? [], "route_accuracy");
  const latestGraphGateRun = latestRunWithMetric(data?.runs ?? [], "graph_gate_pass_rate");
  const efficiency = evaluationEfficiencyView(latestEfficiencyRun);
  const hasRouteMetrics = Boolean(latestRoutingRun);
  const hasGraphGateMetrics = Boolean(latestGraphGateRun);

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
            <MetricCard label="引用正确性" note="最新答案级评测" value={percent(latestAnswerRun ? metricValue(latestAnswerRun, "citation_correctness") : null)} />
            <MetricCard label="拒答正确性" note="最新答案级评测" value={percent(latestAnswerRun ? metricValue(latestAnswerRun, "refusal_correctness") : null)} />
            <MetricCard label="版本有效性" note="状态与生效期一致" value={percent(latestAnswerRun ? metricValue(latestAnswerRun, "version_validity") : null)} />
            <MetricCard label="路由准确率" note="冻结路由矩阵" value={percent(latestRoutingRun ? metricValue(latestRoutingRun, "route_accuracy") : null)} />
            <MetricCard label="重排路由占比" note="高成本路径使用率" value={percent(latestRoutingRun ? metricValue(latestRoutingRun, "rerank_route_rate") : null)} />
            <MetricCard label="关系扩展占比" note="受控图路径使用率" value={percent(latestRoutingRun ? metricValue(latestRoutingRun, "graph_route_rate") : null)} />
            <MetricCard label="图门槛通过率" note="只在 graph 真有增益时才默认开启" value={percent(latestGraphGateRun ? metricValue(latestGraphGateRun, "graph_gate_pass_rate") : null)} />
            <MetricCard label="P95 总延迟" note="最新答案级评测" value={efficiency.p95Latency} />
            <MetricCard label="平均 Token" note="仅统计 Provider 已上报样本" value={efficiency.meanTokens} />
            <MetricCard label="平均估算成本" note={`成本覆盖率 ${efficiency.costCoverage}`} value={efficiency.meanCost} />
            <MetricCard label="评测运行" note="最多展示最近 20 条" value={data.runs.length} />
            <MetricCard label="已索引制度" note="真实 CURRENT manifest" value={data.library_stats.indexed_notes} />
            <MetricCard label="待审核关系" note="不会自动进入检索" value={data.library_stats.relations_proposed} />
          </div>

          <div className="evaluation-notes reveal reveal-3">
            <div><Scale size={18} /><p><strong>路由门槛已显式记录</strong><span>{hasRouteMetrics ? "可以看到 route_accuracy、graph_route_rate 等真实指标。" : "当前没有可用路由结果。"}</span></p></div>
            <div><TimerReset size={18} /><p><strong>图门槛不遮掩失败</strong><span>{hasGraphGateMetrics ? "图路径是否可默认开启，必须由 gate 决策。" : "图路径未达到默认开启门槛时，保持关闭。"}</span></p></div>
          </div>

          {data.runs.length === 0 ? (
            <EmptyState
              title="还没有评测运行"
              detail="可以先运行 scripts/run_ablation.py，再回到这里查看策略对比和真实指标。"
            />
          ) : null}

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
                      <th>引用正确性</th>
                      <th>拒答正确性</th>
                      <th>版本有效性</th>
                      <th>路由准确率</th>
                      <th>图门槛</th>
                      <th>P95 / 检索平均延迟</th>
                      <th>平均成本</th>
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
                        <td>{percent(metricValue(run, "citation_correctness"))}</td>
                        <td>{percent(metricValue(run, "refusal_correctness"))}</td>
                        <td>{percent(metricValue(run, "version_validity"))}</td>
                        <td>{percent(metricValue(run, "route_accuracy"))}</td>
                        <td>{percent(metricValue(run, "graph_gate_pass_rate"))}</td>
                        <td>{numericMetricValue(run, "p95_total_latency_ms") !== null
                          ? `${numericMetricValue(run, "p95_total_latency_ms")!.toFixed(0)} ms`
                          : typeof run.metrics.mean_retrieval_latency_ms === "number"
                            ? `${run.metrics.mean_retrieval_latency_ms.toFixed(0)} ms`
                            : "—"}</td>
                        <td>{evaluationEfficiencyView(run).meanCost}</td>
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
            <div><TimerReset size={18} /><p><strong>成本同屏</strong><span>Token、P95 延迟与估算成本仅按真实上报样本聚合，并同步展示覆盖率。</span></p></div>
          </div>
        </>
      ) : null}
    </div>
  );
}
