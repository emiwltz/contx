export type StatusResponse = {
  api_version: "v1";
  product_version: string;
  health: "healthy" | "degraded";
  collection: {
    paused: boolean;
    paused_at: string | null;
    pause_until: string | null;
    updated_at: string;
    background_enabled: boolean;
    daemon_running: boolean;
    daemon_pid: number | null;
  };
  capabilities: Array<{
    name: string;
    status: string;
    reason_code: string | null;
    settings_path: string | null;
  }>;
  model: {
    provider: string;
    endpoint: string;
    runtime_available: boolean;
    runtime_version: string | null;
    model: string;
    model_available: boolean;
    model_digest: string | null;
    reason_code: string | null;
  };
  counts: Record<string, number>;
  raw_usage_bytes: number;
  memory_usage_bytes: number;
  model_backlog: number;
  model_event_backlog: number;
  abandoned_transformations: number;
  last_processing_run: ProcessingRun | null;
  issues: Array<{ code: string; summary: string; occurred_at: string | null }>;
};

export type Observation = {
  id: string;
  source_type: string;
  activity_state: string;
  captured_at: string;
  started_at: string | null;
  ended_at: string | null;
  app_name: string | null;
  app_bundle_id: string | null;
  window_title: string | null;
  excluded: boolean;
  exclusion_reason: string | null;
  processing_status: string;
  expires_at: string;
  has_raw_artifact: boolean;
};

export type EventRecord = {
  id: string;
  lineage_key: string;
  type: string;
  summary: string;
  facts: Record<string, unknown>;
  started_at: string;
  ended_at: string;
  valid_from: string;
  valid_until: string | null;
  epistemic_status: string;
  confidence: number;
  sensitivity: string;
  projects: string[];
  entities: string[];
  source_observation_ids: string[];
  processing_version: string;
};

export type ActivityResponse = {
  observations: Observation[];
  events: EventRecord[];
  corrections: Array<{
    id: string;
    target_event_id: string;
    event_lineage_key: string;
    summary: string;
    reason: string;
    supersedes_correction_id: string | null;
    created_at: string;
  }>;
};

export type PatternRecord = {
  id: string;
  type: string;
  summary: string;
  window_start: string;
  window_end: string;
  confidence: number;
  sensitivity: string;
  evidence_count: number;
  source_event_ids: string[];
  projects: string[];
  metrics: Record<string, number | string>;
  valid_from: string;
  valid_until: string | null;
  status: string;
};

export type Candidate = {
  id: string;
  text: string;
  source_type: string;
  source_ids: string[];
  confidence: number;
  sensitivity: string;
  score: number;
  status: string;
  rejection_reason: string | null;
  created_at: string;
  processed_at: string | null;
};

export type MemoryRecord = {
  id: string;
  backend_id: string;
  candidate_id: string;
  text: string;
  confidence: number;
  status: string;
  supersedes_memory_id: string | null;
  created_at: string;
  pattern_ids: string[];
  event_ids: string[];
  observation_ids: string[];
};

export type MemoryResponse = {
  candidates: Candidate[];
  memories: MemoryRecord[];
  corrections: Array<{
    candidate_id: string;
    target_memory_id: string;
    provider: string;
    model: string;
    model_digest: string;
    prompt_version: string;
    output_schema_version: string;
    started_at: string;
    ended_at: string;
    wall_duration_ms: number;
  }>;
};

export type WakeResponse = {
  content: string;
  complete: boolean;
  maintenance_required: boolean;
  snapshot: number | null;
  next_part: number | null;
  active_memory_count: number;
  projection_generation: string;
};

export type PrivacyResponse = {
  raw_artifacts: Array<{
    observation_id: string;
    source_type: string;
    captured_at: string;
    expires_at: string;
    size_bytes: number;
    available: boolean;
  }>;
  transformations: Array<{
    id: string;
    source_observation_ids: string[];
    provider: string;
    endpoint: string;
    configured_model: string;
    runtime_version: string | null;
    resolved_model: string | null;
    model_digest: string | null;
    prompt_version: string;
    output_schema_version: string;
    image_sha256: string;
    status: string;
    interpretation: {
      summary: string;
      activity_type: string;
      observed_facts: string[];
      inferred_context: string[];
      projects: string[];
      entities: string[];
      sensitivity: string;
      sensitive_categories: string[];
      confidence: number;
      memory_relevance: number;
    } | null;
    attempt_count: number;
    started_at: string | null;
    ended_at: string | null;
    wall_duration_ms: number | null;
    last_error_code: string | null;
    created_at: string;
    updated_at: string;
  }>;
  candidate_decisions: Array<{
    id: string;
    candidate_id: string;
    processing_run_id: string;
    policy_version: string;
    status: string;
    reason: string | null;
    created_at: string;
  }>;
};

export type ProcessingRun = {
  id: string;
  pipeline: string;
  version: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  input_count: number;
  output_count: number;
  error_code: string | null;
  error_summary: string | null;
};

export type AgentResponse = {
  proposals: Array<{
    id: string;
    agent_id: string;
    agent_role: string;
    text: string;
    reference_type: string | null;
    reference_id: string | null;
    status: string;
    reason: string | null;
    created_at: string;
    processed_at: string | null;
  }>;
  validations: Array<{
    proposal_id: string;
    candidate_id: string | null;
    provider: string;
    model: string;
    model_digest: string;
    prompt_version: string;
    output_schema_version: string;
    decision: string;
    reason_code: string;
    confidence: number;
    active_memory_count: number;
    started_at: string;
    ended_at: string;
    wall_duration_ms: number;
  }>;
};

export type Exclusion = {
  id: string;
  rule_type: string;
  pattern: string;
  scope: string;
  enabled: boolean;
  built_in: boolean;
  created_at: string;
  updated_at: string;
};

export type SettingsResponse = {
  config_version: number;
  collection: Record<string, unknown>;
  model: Record<string, unknown>;
  events: Record<string, unknown>;
  memory: Record<string, unknown>;
  api: Record<string, unknown>;
};

export type WorkspaceData = {
  status: StatusResponse;
  activity: ActivityResponse;
  patterns: { patterns: PatternRecord[] };
  memory: MemoryResponse;
  privacy: PrivacyResponse;
  processing: { runs: ProcessingRun[] };
  agent: AgentResponse;
  exclusions: Exclusion[];
  settings: SettingsResponse;
};

type ApiErrorBody = { detail?: string };

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<T>(response);
}

export async function apiMutation<T>(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  payload: Record<string, unknown>,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (response.status === 204) {
    return undefined as T;
  }
  return parseResponse<T>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Erreur locale ${response.status}`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      detail = body.detail ?? detail;
    } catch {
      // A non-JSON infrastructure response has no private detail to expose.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}
