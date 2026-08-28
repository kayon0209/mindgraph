import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Scale, TimerReset } from "lucide-react";

import { BarComparison } from "../components/BarComparison";
import { ContextHint, EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from "../components/Primitives";
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
  // 运行记录表默认只留核心列，次要列（答案质量/成本）按需展开，避免密不透风的 13 列表
  const [showAllColumns, setShowAllColumns] = useState(false);

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

  // 研究项④：指标墙不再整墙展示「—」。每张卡带「如何开启」说明，
  // 有值的正常展示，无值的折叠进「未启用指标」分组，把空态变成行动召唤。
  type MetricDef = { label: string; note: string; value: string | number; enableHint?: string };
  const metricDefs: MetricDef[] = [
    { label: "当前最佳召回率 Top5", note: "最近各策略运行", value: percent(bestRecall), enableHint: "运行检索对比评测后显示。" },
    { label: "引用正确性", note: "最新答案级评测", value: percent(latestAnswerRun ? metricValue(latestAnswerRun, "citation_correctness") : null), enableHint: "运行回答质量评测后显示。" },
    { label: "拒答正确性", note: "最新答案级评测", value: percent(latestAnswerRun ? metricValue(latestAnswerRun, "refusal_correctness") : null), enableHint: "运行回答质量评测后显示。" },
    { label: "版本有效性", note: "状态与生效期一致", value: percent(latestAnswerRun ? metricValue(latestAnswerRun, "version_validity") : null), enableHint: "运行包含版本校验的回答质量评测后显示。" },
    { label: "路由准确率", note: "冻结路由矩阵", value: percent(latestRoutingRun ? metricValue(latestRoutingRun, "route_accuracy") : null), enableHint: "运行包含策略记录的检索评测后显示。" },
    { label: "重排路由占比", note: "高成本路径使用率", value: percent(latestRoutingRun ? metricValue(latestRoutingRun, "rerank_route_rate") : null), enableHint: "运行包含策略记录的检索评测后显示。" },
    { label: "关系扩展占比", note: "受控图路径使用率", value: percent(latestRoutingRun ? metricValue(latestRoutingRun, "graph_route_rate") : null), enableHint: "运行包含策略记录的检索评测后显示。" },
    { label: "图门槛通过率", note: "只在 graph 真有增益时才默认开启", value: percent(latestGraphGateRun ? metricValue(latestGraphGateRun, "graph_gate_pass_rate") : null), enableHint: "启用关联扩展并运行对应评测后显示。" },
    { label: "耗时（毫秒）", note: "最新答案级评测", value: efficiency.p95Latency, enableHint: "运行回答质量评测后显示。" },
    { label: "平均 Token", note: "仅统计 Provider 已上报样本", value: efficiency.meanTokens, enableHint: "运行回答质量评测后显示。" },
    { label: "平均估算成本", note: `成本覆盖率 ${efficiency.costCoverage}`, value: efficiency.meanCost, enableHint: "运行回答质量评测后显示。" },
  ];
  const filledMetrics = metricDefs.filter((metric) => String(metric.value) !== "—");
  const emptyMetrics = metricDefs.filter((metric) => String(metric.value) === "—");

  return (
    <div className="page evaluation-page">
      <PageHeader
        eyebrow="检索质量追踪"
        title="质量账本"
        description="展示真实评测结果，每个指标都对应一次实际运行记录。"
        actions={
          <button className="button secondary" onClick={() => void load()} type="button">
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新账本
          </button>
        }
      />

      <ContextHint storageKey="mindgraph.hint.evaluation">
        指标来自真实的评测运行，与线上问答使用相同的检索管线。页面不会用示例数字填充。
      </ContextHint>

      {loading ? <LoadingState label="读取评测运行与指标" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {!loading && !error && data ? (
        <>
          <div className="metrics-grid reveal reveal-2">
            {/* 库统计永远是真实数据，与评测指标分开展示 */}
            <MetricCard label="评测次数" note="最近 20 次" value={data.runs.length} />
            <MetricCard label="已索引制度" note="实时索引状态" value={data.library_stats.indexed_notes} />
            <MetricCard label="待审核关系" note="不会自动进入检索" value={data.library_stats.relations_proposed} />
            {filledMetrics.map((metric) => (
              <MetricCard key={metric.label} label={metric.label} note={metric.note} value={metric.value} />
            ))}
          </div>

          {data.runs.length === 0 ? (
            <EmptyState
              title="还没有评测运行"
              detail="先运行检索对比或回答质量评测，再回到这里查看结果。"
            />
          ) : null}

          {emptyMetrics.length ? (
            <details className="metrics-empty-fold reveal reveal-3">
              <summary>还有 {emptyMetrics.length} 项指标未启用 · 查看如何开启</summary>
              <ul className="metrics-empty-list">
                {emptyMetrics.map((metric) => (
                  <li key={metric.label}>
                    <strong>{metric.label}</strong>
                    <span>{metric.enableHint}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          <div className="evaluation-notes reveal reveal-3">
            <div><Scale size={18} /><p><strong>检索策略已记录</strong><span>{hasRouteMetrics ? "可查看各检索策略的真实表现。" : "当前没有可用路由结果。"}</span></p></div>
            <div><TimerReset size={18} /><p><strong>保守启用扩展检索</strong><span>{hasGraphGateMetrics ? "关联扩展仅在确有增益时才默认开启。" : "图路径未达到默认开启门槛时，保持关闭。"}</span></p></div>
          </div>

          {comparisonRuns.length ? (
            <section className="comparison-section reveal reveal-3">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">策略对比</p>
                  <h2>策略对比</h2>
                </div>
                <span>{comparisonRuns.length} 个策略</span>
              </div>
              <BarComparison runs={comparisonRuns} />
            </section>
          ) : (
            <EmptyState
              title="还没有可展示的评测结果"
              detail="先运行检索对比评测。页面不会用示例数值替代真实结果。"
            />
          )}

          <section className="runs-section reveal reveal-4">
            <div className="section-heading">
              <div>
                <p className="eyebrow">运行历史记录</p>
                <h2>运行记录</h2>
              </div>
              <div className="runs-heading-actions">
                {latest ? <span>最近 · {latest.strategy}</span> : null}
                <button
                  className="button ghost small"
                  onClick={() => setShowAllColumns((value) => !value)}
                  type="button"
                  aria-pressed={showAllColumns}
                >
                  {showAllColumns ? "收起次要列" : "显示全部列"}
                </button>
              </div>
            </div>
            {data.runs.length ? (
              <div className="run-table-wrap">
                <table className="run-table">
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th>数据集</th>
                      <th>Top 5 命中率</th>
                      <th>平均排名</th>
                      {showAllColumns ? <th>引用正确性</th> : null}
                      {showAllColumns ? <th>拒答正确性</th> : null}
                      {showAllColumns ? <th>版本有效性</th> : null}
                      <th>策略准确率</th>
                      <th>扩展门槛</th>
                      <th>检索耗时</th>
                      {showAllColumns ? <th>平均成本</th> : null}
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
                        {showAllColumns ? <td>{percent(metricValue(run, "citation_correctness"))}</td> : null}
                        {showAllColumns ? <td>{percent(metricValue(run, "refusal_correctness"))}</td> : null}
                        {showAllColumns ? <td>{percent(metricValue(run, "version_validity"))}</td> : null}
                        <td>{percent(metricValue(run, "route_accuracy"))}</td>
                        <td>{percent(metricValue(run, "graph_gate_pass_rate"))}</td>
                        <td>{numericMetricValue(run, "p95_total_latency_ms") !== null
                          ? `${numericMetricValue(run, "p95_total_latency_ms")!.toFixed(0)} ms`
                          : typeof run.metrics.mean_retrieval_latency_ms === "number"
                            ? `${run.metrics.mean_retrieval_latency_ms.toFixed(0)} ms`
                            : "—"}</td>
                        {showAllColumns ? <td>{evaluationEfficiencyView(run).meanCost}</td> : null}
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
            <div><Scale size={18} /><p><strong>评测不掺水</strong><span>评测标签必须独立标注，不能从结果反推。</span></p></div>
            <div><TimerReset size={18} /><p><strong>成本透明</strong><span>用量与成本按真实上报数据统计。</span></p></div>
          </div>
        </>
      ) : null}
    </div>
  );
}
