export interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
  status: string;
  last_login_at: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AdminUser;
}

export interface DashboardSummary {
  scenario_count: number;
  active_scenario_count: number;
  stage_count: number;
  dynamic_info_count: number;
  intervention_rule_count: number;
  rubric_dimension_count: number;
  rubric_anchor_count: number;
  prompt_template_count: number;
  report_template_count: number;
}

export interface DashboardSessionStatusItem {
  status: string;
  count: number;
}

export interface DashboardAgentStatusItem {
  status: string;
  count: number;
}

export interface DashboardRecentSessionItem {
  session_uuid: string;
  nickname: string;
  scenario_title: string;
  status: string;
  assessment_mode: string;
  turn_count: number;
  agent_trace_count: number;
  report_status: string | null;
  duration_minutes: number | null;
  started_at: string | null;
  updated_at: string;
}

export interface DashboardStageProgressItem {
  stage_title: string;
  ai_turn_count: number;
  user_turn_count: number;
  trace_count: number;
}

export interface DashboardFeedbackAverages {
  realism_score: number | null;
  difficulty_score: number | null;
  naturalness_score: number | null;
  fatigue_score: number | null;
  report_trust_score: number | null;
  overall_satisfaction_score: number | null;
}

export interface DashboardFeedbackCommentItem {
  nickname: string;
  overall_satisfaction_score: number;
  naturalness_score: number;
  report_trust_score: number;
  open_feedback: string;
  submitted_at: string;
}

export interface DashboardAnalytics {
  session_count: number;
  completed_session_count: number;
  in_progress_session_count: number;
  completion_rate: number;
  average_duration_minutes: number | null;
  average_turn_count: number | null;
  dialogue_turn_count: number;
  agent_trace_count: number;
  agent_success_rate: number | null;
  report_count: number;
  score_snapshot_count: number;
  score_result_count: number;
  score_evidence_count: number;
  status_distribution: DashboardSessionStatusItem[];
  agent_status_distribution: DashboardAgentStatusItem[];
  recent_sessions: DashboardRecentSessionItem[];
  stage_progress: DashboardStageProgressItem[];
  feedback_count: number;
  feedback_coverage_rate: number;
  feedback_averages: DashboardFeedbackAverages;
  low_satisfaction_count: number;
  recent_feedback_comments: DashboardFeedbackCommentItem[];
}

export interface AdminSessionListItem {
  session_uuid: string;
  nickname: string;
  scenario_code: string;
  scenario_title: string;
  status: string;
  assessment_mode: string;
  turn_count: number;
  agent_trace_count: number;
  report_status: string | null;
  review_status: "pending" | "in_review" | "completed" | "needs_adjudication";
  review_decision: "valid" | "needs_adjudication" | "exclude" | null;
  min_ai_confidence: number | null;
  expert_score_count: number;
  expert_score_target_count: number;
  expert_score_completion_rate: number;
  duration_minutes: number | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface AdminSessionListResponse {
  items: AdminSessionListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminReviewTurn {
  turn_id: number;
  turn_index: number;
  stage_code: string | null;
  stage_title: string | null;
  speaker: string;
  content: string;
  content_type: string;
  source_agent_trace_id: number | null;
  intervention_rule_code: string | null;
  dynamic_info_code: string | null;
  client_turn_id: string | null;
  answer_duration_ms: number | null;
  created_at: string;
}

export interface AdminReviewTrace {
  trace_id: number;
  stage_code: string | null;
  stage_title: string | null;
  trigger_turn_id: number | null;
  agent_name: string;
  generation_mode: string | null;
  ai_generation_weight: number | null;
  config_snapshot_json: Record<string, unknown> | null;
  input_json: Record<string, unknown>;
  output_json: Record<string, unknown> | null;
  raw_output: string | null;
  status: string;
  error_code: string | null;
  fallback_type: string | null;
  fallback_reason?: string | null;
  prompt_template_id: number | null;
  parent_trace_id?: number | null;
  interviewer_style_version?: string | null;
  validation_codes?: string[];
  model_name: string | null;
  duration_ms: number | null;
  selected_rule_code: string | null;
  selected_dynamic_info_code: string | null;
  created_at: string;
}

export interface AdminReviewEvidence {
  evidence_id: number;
  dialogue_turn_id: number | null;
  evidence_text: string;
  evidence_type: string;
  explanation: string | null;
  created_at: string;
}

export interface AdminReviewScoreResult {
  score_result_id: number;
  dimension_key: string;
  dimension_name: string;
  score: number | null;
  assessment_status: "scored" | "insufficient_evidence" | string;
  reason: string;
  confidence: number | null;
  evidence_sufficiency_index: number | null;
  score_kind: "supported" | "provisional" | "unobserved" | null;
  scoring_source: string;
  evidence: AdminReviewEvidence[];
  created_at: string;
}

export interface AdminReviewScoreSnapshot {
  snapshot_id: number;
  stage_code: string | null;
  stage_title: string | null;
  dialogue_turn_id: number | null;
  snapshot_type: string;
  summary: string | null;
  trend_analysis: string | null;
  agent_trace_id: number | null;
  results: AdminReviewScoreResult[];
  created_at: string;
}

export type HumanReviewStatus =
  | "pending"
  | "in_review"
  | "completed"
  | "needs_adjudication";

export type HumanReviewDecision = "valid" | "needs_adjudication" | "exclude";

export interface HumanReview {
  status: HumanReviewStatus;
  decision: HumanReviewDecision | null;
  notes: string | null;
  reviewer_id: number | null;
  reviewer_name: string | null;
  completed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ExpertScoreTarget {
  stage_code: string;
  stage_title: string;
  dimension_key: string;
  dimension_name: string;
  ai_score: number | null;
  ai_confidence: number | null;
}

export interface ExpertScore {
  annotation_id: number;
  stage_code: string;
  stage_title: string;
  dimension_key: string;
  dimension_name: string;
  annotator_id: number;
  annotator_name: string;
  is_current_annotator: boolean;
  assessment_status: "scored" | "insufficient_evidence";
  score: number | null;
  evidence_ids: number[];
  bars_reason: string;
  next_level_gap: string | null;
  annotator_confidence: "high" | "medium" | "low";
  review_flag: boolean;
  review_reason: string | null;
  source: "manual" | "csv_import" | string;
  import_batch_id: string | null;
  ai_score: number | null;
  ai_confidence: number | null;
  score_difference: number | null;
  created_at: string;
  updated_at: string;
}

export interface ExpertScoreWrite {
  stage_code: string;
  dimension_key: string;
  assessment_status: "scored" | "insufficient_evidence";
  score: number | null;
  evidence_ids: number[];
  bars_reason: string;
  next_level_gap: string | null;
  annotator_confidence: "high" | "medium" | "low";
  review_flag: boolean;
  review_reason: string | null;
}

export interface ExpertScoreBatchResponse {
  saved_count: number;
  imported_count: number;
  import_batch_id: string | null;
  items: ExpertScore[];
}

export interface AdminSessionReviewResponse {
  session: {
    session_uuid: string;
    nickname: string;
    scenario_code: string;
    scenario_title: string;
    scenario_version: string;
    scenario_source_type: string;
    base_scenario_id: number | null;
    occupation_category: string | null;
    occupation: string | null;
    scenario_generation_status: string | null;
    scenario_cache_hit: boolean;
    scenario_fallback_used: boolean;
    status: string;
    assessment_mode: string;
    flow_version: "legacy_v2" | "progressive_v3" | string;
    interviewer_style_version: string;
    state_version: number;
    current_stage_code: string | null;
    current_stage_title: string | null;
    started_at: string | null;
    completed_at: string | null;
    duration_minutes: number | null;
    created_at: string;
    updated_at: string;
  };
  turns: AdminReviewTurn[];
  traces: AdminReviewTrace[];
  score_snapshots: AdminReviewScoreSnapshot[];
  report: {
    status: string;
    summary: string | null;
    report_json: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  } | null;
  feedback: {
    realism_score: number;
    difficulty_score: number;
    naturalness_score: number;
    fatigue_score: number;
    report_trust_score: number;
    overall_satisfaction_score: number;
    open_feedback: string | null;
    submitted_at: string;
  } | null;
  human_review: HumanReview;
  expert_score_targets: ExpertScoreTarget[];
  expert_scores: ExpertScore[];
  progressive_audit: Record<string, unknown> | null;
}

export interface RubricAnchor {
  id: number;
  dimension_id: number;
  score_level: number;
  level_name: string;
  behavior_desc: string;
  evidence_examples: string[] | null;
  counter_examples: string[] | null;
  status: string;
  updated_at: string;
}

export interface RubricDimension {
  id: number;
  dimension_key: string;
  name: string;
  definition: string;
  observable_behaviors: string[] | Record<string, unknown>;
  invalid_evidence_desc: string | null;
  version: string;
  status: string;
  updated_at: string;
  anchors: RubricAnchor[];
}

export interface ScenarioListItem {
  id: number;
  scenario_code: string;
  title: string;
  target_audience: string;
  scenario_type: string;
  difficulty_level: string;
  estimated_minutes: number;
  rotation_weight: number;
  is_default: boolean;
  version: string;
  status: string;
  stage_count: number;
  updated_at: string;
  source_type: "seeded" | "seeded_fallback" | "ai_base" | "ai_adapted" | string;
  occupation_category: string | null;
  occupation: string | null;
  occupation_key: string | null;
  generation_prompt_version: string | null;
  generation_model: string | null;
  is_immutable: boolean;
  validation_status: string | null;
  usage_count: number;
  last_used_at: string | null;
}

export interface Scenario extends Omit<ScenarioListItem, "stage_count" | "occupation" | "validation_status" | "usage_count" | "last_used_at"> {
  background: string;
  base_scenario_id: number | null;
  generation_metadata_json: Record<string, unknown> | null;
}

export interface DimensionBinding {
  dimension_id: number;
  observe_role: string;
  weight: number | null;
  dimension_key: string;
  dimension_name: string;
}

export interface ScenarioStage {
  id: number;
  scenario_id: number;
  stage_code: string;
  stage_order: number;
  title: string;
  stage_goal: string;
  context: string;
  main_question: string;
  context_generation_mode: string;
  context_ai_weight: number;
  context_generation_constraints_json: Record<string, unknown> | null;
  max_followups: number;
  estimated_minutes: number;
  exit_criteria_json: Record<string, unknown> | null;
  status: string;
  updated_at: string;
  dimensions: DimensionBinding[];
}

export interface StageDynamicInfo {
  id: number;
  stage_id: number;
  info_code: string;
  title: string;
  content: string;
  info_type: string;
  trigger_condition: string | null;
  priority: number;
  status: string;
  updated_at: string;
}

export interface StageInterventionRule {
  id: number;
  stage_id: number;
  rule_code: string;
  rule_type: string;
  trigger_condition: string | null;
  strategy_direction: string;
  sample_question: string | null;
  question_generation_mode: string;
  question_ai_weight: number;
  question_generation_constraints_json: Record<string, unknown> | null;
  fallback_question: string | null;
  exit_prompt: string | null;
  priority: number;
  max_use_count: number | null;
  status: string;
  updated_at: string;
}
