import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";

import { api } from "../lib/api";
import { governanceCaseView } from "../lib/knowledge-governance";
import { policyGovernanceView } from "../lib/metrics";
import type { GovernanceCase, PolicyGovernance as PolicyGovernanceValue } from "../types";

export function PolicyGovernance({ compact = false, value }: { compact?: boolean; value: PolicyGovernanceValue }) {
  const view = policyGovernanceView(value);
  if (compact) {
    return (
      <span className="governance-inline">
        <span className={`governance-dot ${view.statusTone}`} />
        <span>{view.statusLabel}</span>
        <small>{value.version ? `V${value.version}` : "无版本"}</small>
      </span>
    );
  }

  return (
    <section className={`governance-card ${value.metadata_complete ? "complete" : "incomplete"}`}>
      <div className="governance-card-heading">
        {value.metadata_complete ? <ShieldCheck size={18} /> : <AlertTriangle size={18} />}
        <div>
          <strong>{view.completenessLabel}</strong>
          <span>{view.period}</span>
        </div>
      </div>
      {view.issueLabels.length ? (
        <ul>{view.issueLabels.map((issue) => <li key={issue}>{issue}</li>)}</ul>
      ) : (
        <p>制度族、责任部门、版本、生效期和状态均已登记。</p>
      )}
      {value.lifecycle_state ? (
        <p>截至 {value.evaluated_on || "当前响应日期"}：{value.lifecycle_state} · {value.disposition}</p>
      ) : null}
    </section>
  );
}

export function GovernanceQueue({ onChanged }: { onChanged: () => Promise<void> }) {
  const [cases, setCases] = useState<GovernanceCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyCase, setBusyCase] = useState<string | null>(null);
  const [canonicalChoices, setCanonicalChoices] = useState<Record<string, string>>({});

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.governanceCases();
      setCases(result.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "治理队列读取失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const decide = async (item: GovernanceCase, decision: "confirm" | "reject" | "revoke") => {
    setBusyCase(item.case_id);
    setError("");
    try {
      if (decision === "revoke") {
        if (item.status !== "confirmed" && item.status !== "rejected") return;
        await api.revokeGovernanceCase(item.case_id, item.status);
      } else {
        if (item.status !== "proposed") return;
        const canonical = decision === "confirm" && item.case_type === "exact_duplicate"
          ? canonicalChoices[item.case_id]
          : undefined;
        await api.resolveGovernanceCase(item.case_id, item.status, decision, canonical);
      }
      await Promise.all([load(), onChanged()]);
    } catch (decisionError) {
      setError(decisionError instanceof Error ? decisionError.message : "治理操作失败，请重试");
    } finally {
      setBusyCase(null);
    }
  };

  return (
    <div className="governance-queue">
      <div className="governance-queue-toolbar">
        <span>{loading ? "正在读取可见治理事项…" : `${cases.length} 个可见事项`}</span>
        <button onClick={() => void load()} type="button"><RefreshCw size={15} />刷新</button>
      </div>
      {error ? <p className="governance-queue-error">{error}；当前列表已保留，可重试。</p> : null}
      {!loading && cases.length === 0 ? <p className="rail-placeholder">当前没有可见治理事项。</p> : null}
      {cases.map((item) => {
        const view = governanceCaseView(item);
        return (
          <details className="governance-case" key={item.case_id}>
            <summary>
              <strong>{view.reasonLabel}</strong>
              <span>{item.status} · {item.policy_key || "未归类制度"}</span>
            </summary>
            <div className="governance-case-body">
              <code>{item.case_id}</code>
              <ul>
                {item.participants.map((participant) => (
                  <li key={participant.note_id}>
                    <code>{participant.note_id}</code>
                    <span>V{participant.document_version || "—"}</span>
                    <span>{participant.effective_from || "未设置"} — {participant.effective_to || "长期有效"}</span>
                  </li>
                ))}
              </ul>
              {item.case_type === "exact_duplicate" && item.capabilities.can_resolve ? (
                <label className="governance-canonical-choice">
                  Canonical note
                  <select
                    onChange={(event) => setCanonicalChoices((current) => ({
                      ...current,
                      [item.case_id]: event.target.value,
                    }))}
                    value={canonicalChoices[item.case_id] || ""}
                  >
                    <option value="">请选择 canonical note</option>
                    {item.participants.map((participant) => (
                      <option key={participant.note_id} value={participant.note_id}>
                        {participant.note_id} · V{participant.document_version || "—"}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <div className="governance-actions">
                {view.actions.map((action) => (
                  <button
                    disabled={
                      busyCase === item.case_id
                      || (action === "confirm" && item.case_type === "exact_duplicate"
                        && !canonicalChoices[item.case_id])
                    }
                    key={action}
                    onClick={() => void decide(item, action)}
                    type="button"
                  >
                    {action === "confirm" ? "确认" : action === "reject" ? "拒绝" : "撤销决定"}
                  </button>
                ))}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}
