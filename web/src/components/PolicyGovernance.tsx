import { AlertTriangle, ShieldCheck } from "lucide-react";

import { policyGovernanceView } from "../lib/metrics";
import type { PolicyGovernance as PolicyGovernanceValue } from "../types";

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
    </section>
  );
}
