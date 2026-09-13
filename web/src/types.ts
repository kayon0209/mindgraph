export type ViewId = "chat" | "knowledge" | "graph" | "evaluation" | "relations";

export type HealthStatus = {
  status?: string;
  service?: string;
  version?: string;
};

/** /config/public 中与前端状态展示相关的字段（研究项⑭：模型状态前置） */
export type PublicConfig = {
  chat_models?: Array<{
    provider: string;
    model: string;
    configured?: boolean;
    verified?: boolean;
  }>;
  default_chat_provider?: string;
  /** P0-1 后续：assist 深度核对可用性探测改读此布尔（零副作用），不再 POST agent stream */
  assist_agent_enabled?: boolean;
};

export type ChatRequest = {
  question: string;
  retrieval_strategy: "auto" | "dense" | "bm25" | "hybrid" | "hybrid_rerank";
  final_top_k: number;
  include_retrieval_trace: boolean;
  include_historical: boolean;
  graph_enabled: boolean;
  /** 计划 4.4：版本/冲突问题可用两跳图扩展（后端 ge=1 le=2） */
  graph_hops?: number;
  /** ISO 日期（YYYY-MM-DD），用于版本/冲突判定 */
  query_date?: string;
  /** 计划 3.3：显式问题类型（compound_question 触发拆解等） */
  query_type?: string;
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
  /** 后端字段为 source_document_version（关系所在文档的版本） */
  source_document_version?: string | null;
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
  /** M0：确定性检查产生的告警条目（如 citation_fidelity:missing_marks=9） */
  warnings?: string[];
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
  /** M0 契约基线：机器可判定错误码，取值见后端 ErrorCode（additive） */
  error_code?: string | null;
  /** M0 契约基线：回答中的 [citation-N] 全部命中返回引用集为 true；无引用且
   *  无标注时为 null（warning-first，M2 前不阻断） */
  citation_fidelity?: boolean | null;
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

/** M2：确定性 Assist 的执行步骤（plan_created.data.steps 元素；label 为用户语言） */
export type AssistStep = {
  name: string;
  label: string;
};

/** M2：plan_created 事件数据 */
export type AssistPlan = {
  steps: AssistStep[];
  route: string;
  reason_codes?: string[];
  routing_ms?: number;
};

/** M2：单条工具执行记录（tool_call_started/finished 累积） */
export type AssistToolCall = {
  step: string;
  label: string;
  status: "running" | "ok" | "failed" | "denied" | "timeout";
  result_state?: string;
  latency_ms?: number;
};

/** M2：clarification_required 事件数据（P0-1：提交补充信息 = 新的补充问题请求，
 *  当前无服务端恢复，前端不发送 resume_from） */
export type AssistClarification = {
  clarification_id: string;
  questions: string[];
  context_hash: string;
  expires_at: string;
};

/** M2：citation_integrity_checked 事件数据 */
export type AssistIntegrity = {
  passed: boolean;
  applicable: boolean;
  checks?: {
    unknown_markers?: string[];
    duplicate_markers?: string[];
    malformed_markers?: string[];
    unused_citations?: string[];
  };
};

/** SSE usage 事件（后端 UsageMetrics 的 JSON 形态） */
export type UsageInfo = {
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  estimated_cost?: number | null;
  currency?: string | null;
  usage_source?: string;
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
    /** P1：当前激活索引版本与构建时间，用于索引新鲜度展示 */
    index_version?: string | null;
    index_built_at?: string | null;
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
  /** 审核者需要看到证据原文/章节，而不是只有 chunk id（P3-24） */
  evidence_span?: string | null;
  evidence_section?: string | null;
  conflict?: boolean;
};

export type ProposedRelationsResponse = {
  proposed: RelationItem[];
  adoption_trend: Array<{ month: string; count: number }>;
};

export type ConfirmedRelationsResponse = { confirmed: RelationItem[] };

/** POST /knowledge/documents 上传回执（**已弃用入口**，仅保留类型给历史客户端） */
export type DocumentRecord = {
  document_id?: string;
  title?: string;
  category?: string;
  status?: string;
  [key: string]: unknown;
};

/**
 * POST /knowledge/versions 回执（后端 DocumentVersionModel 的前端视图）。
 *
 * 只声明前端真正用到的字段 + 索引签名：后端模型有二十来个字段，逐一对齐会变成
 * 一份需要跟着后端改的镜像，漏一个字段就编译不过。这里要的是"用到的那几个类型明确"。
 */
export type DocumentVersionModel = {
  document_id: string;
  logical_document_id: string;
  version: string;
  title: string;
  status: string;
  file_type?: string;
  knowledge_category?: string;
  authority_level?: string;
  parsing_diagnostics?: {
    status?: string;
    failure_reason?: string;
    warnings?: string[];
    ocr_required_pages?: number[];
    [key: string]: unknown;
  };
  created_at?: string;
  [key: string]: unknown;
};

/** POST /mindgraph/relations/extract 结果（HITL：仅写 proposed） */
export type ExtractRelationsResult = {
  ok?: boolean;
  created?: number;
  dry_run?: boolean;
  method?: string;
  reason?: string;
  [key: string]: unknown;
};

/** 覆盖缺口：用户问过但语料未覆盖的概念（来自 query_logs 规则式挖掘） */
export type ConceptGap = {
  term: string;
  seen_count: number;
  first_seen: string;
  last_seen: string;
  sample_question_hash?: string | null;
};

/** POST /mindgraph/relations/mine-questions 结果（只产 proposed CO_ASKED，需人工确认） */
export type MineQuestionsResult = {
  ok?: boolean;
  dry_run?: boolean;
  trigger?: string;
  mined?: number;
  proposed_created?: number;
  skipped_existing?: number;
  gap_terms?: number;
  gaps?: ConceptGap[];
  reason?: string;
  [key: string]: unknown;
};

export type ConceptGapsResponse = { gaps: ConceptGap[]; total: number };

/** M4-A：后台任务（AGENT_TASKS_ENABLED；状态机见 domain.task_models.TaskStatus） */
export type AgentTask = {
  task_id: string;
  task_type: string;
  status: "queued" | "running" | "completed" | "completed_with_conflicts" | "completed_empty" | "failed" | "cancelled";
  result_state?: string | null;
  constraints: Record<string, unknown>;
  attempt_count: number;
  cancel_requested: boolean;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentArtifactMeta = {
  artifact_id: string;
  kind: string;
  title: string;
  visibility: string;
  checksum: string;
  created_at: string;
};

export type AgentArtifactContent = {
  artifact_id: string;
  task_id: string;
  kind: string;
  title: string;
  content: {
    document_query?: string | null;
    as_of?: string | null;
    matched_documents?: number;
    conflict_count?: number;
    conflicts?: Array<{ policy_key?: string | null; versions: Array<Record<string, unknown>> }>;
  };
  evidence_snapshot: Array<{
    citation_id: string;
    document_name?: string | null;
    document_version?: string | null;
    effective_from?: string | null;
    effective_to?: string | null;
    policy_status?: string | null;
    policy_key?: string | null;
    excerpt?: string | null;
  }>;
  citations: Array<Record<string, unknown>>;
  checksum: string;
  created_at: string;
};
