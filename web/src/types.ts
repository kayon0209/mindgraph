export type ViewId = "chat" | "knowledge" | "evaluation" | "relations";

export type HealthStatus = {
  status?: string;
  service?: string;
  version?: string;
};

export type ChatRequest = {
  question: string;
  retrieval_strategy: "auto" | "dense" | "bm25" | "hybrid" | "hybrid_rerank";
  final_top_k: number;
  include_retrieval_trace: boolean;
  include_historical: boolean;
  graph_enabled: boolean;
};

export type Citation = {
  citation_id: string;
  document_id: string;
  document_name: string;
  chunk_id: string;
  section_path?: string | null;
  excerpt: string;
  final_rank: number;
  retrieval_score?: number | null;
  reranker_score?: number | null;
  document_version?: string | null;
  owner?: string | null;
  effective_from?: string | null;
  effective_to?: string | null;
  policy_status?: string | null;
  policy_key?: string | null;
  authority_level?: string | null;
  vault_path?: string | null;
};

export type GraphLink = {
  relation_id?: string;
  source_note_id: string;
  source_title?: string;
  relation_type: string;
  target_note_id: string;
  target_title?: string;
  confidence?: number;
  evidence_chunk_id?: string | null;
  evidence_span?: string | null;
  evidence_section?: string | null;
  document_version?: string | null;
  status?: string | null;
  hop?: number;
};

export type PolicyConflictVersion = {
  note_id: string;
  title: string;
  vault_path: string;
  version: string | null;
  effective_from: string | null;
  effective_to: string | null;
  policy_status: string;
  owner: string | null;
};

export type PolicyConflict = {
  policy_key: string;
  as_of: string;
  versions: PolicyConflictVersion[];
};

export type RouteDecision = {
  mode: "adaptive" | "manual";
  route: string;
  requested_strategy: string;
  selected_strategy: string;
  graph_enabled: boolean;
  reasons: string[];
  estimated_cost_tier?: string;
  estimated_latency_tier?: string;
  degraded?: boolean;
};

export type RetrievalTrace = {
  requested_strategy: string;
  actual_strategy: string;
  candidate_counts: Record<string, number>;
  stage_latency_ms: Record<string, number>;
  degraded: boolean;
  degradation_reason?: string | null;
  index_version?: string | null;
  graph_enabled: boolean;
  graph_hops?: number;
  graph_evidence?: { relation_id?: string; evidence_chunk_id?: string | null; evidence_span?: string | null; evidence_section?: string | null; status?: string | null }[];
  graph_links: GraphLink[];
  policy_conflicts?: PolicyConflict[];
  route_decision?: RouteDecision;
};

export type AnswerResult = {
  request_id: string;
  question: string;
  answer: string;
  result_state: string;
  citations: Citation[];
  retrieval_trace?: RetrievalTrace | null;
  timing: { total_ms: number; ttft_ms?: number | null };
  requested_strategy: string;
  actual_strategy: string;
  degraded: boolean;
  degradation_reason?: string | null;
  model: string;
  index_version?: string | null;
};

export type StreamEvent = {
  request_id?: string;
  event: string;
  timestamp?: string;
  data: Record<string, unknown>;
};

export type NoteItem = {
  id: string;
  title: string;
  vault_path: string;
  category: string;
  access_level: string;
  status: string;
  chunk_count: number;
  updated: string;
  excerpt: string;
  governance: PolicyGovernance;
};

export type PolicyGovernance = {
  policy_key: string | null;
  owner: string | null;
  version: string | null;
  effective_from: string | null;
  effective_to: string | null;
  policy_status: string;
  metadata_complete: boolean;
  issues: string[];
};

export type NoteDetail = NoteItem & {
  created: string;
  outgoing_relations: Array<{
    target_id: string;
    target_title: string;
    relation_type: string;
    confidence?: number;
  }>;
  incoming_relations: Array<{
    source_id: string;
    source_title: string;
    relation_type: string;
    confidence?: number;
  }>;
};

export type EvaluationMetricMap = Record<string, number | string | null | undefined>;

export type EvaluationRun = {
  run_id: string;
  status: string;
  dataset: string;
  strategy: string;
  model?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  metrics: EvaluationMetricMap;
};

export type EvaluationResponse = {
  library_stats: {
    notes_total: number;
    chunks_total: number;
    relations_confirmed: number;
    relations_proposed: number;
    indexed_notes: number;
  };
  runs: EvaluationRun[];
};

export type RelationItem = {
  id: string;
  source: string;
  target: string;
  source_id: string;
  target_id: string;
  type: string;
  confidence?: number | null;
  proposed_at?: string;
  evidence_chunk_id?: string | null;
  conflict?: boolean;
};

export type ProposedRelationsResponse = {
  proposed: RelationItem[];
  adoption_trend: Array<{ month: string; count: number }>;
};

export type ConfirmedRelationsResponse = { confirmed: RelationItem[] };
