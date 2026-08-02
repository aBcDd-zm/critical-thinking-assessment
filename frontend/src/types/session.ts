import type { ReportOutput } from "./report";

export interface ScenarioSummary {
  scenario_code: string;
  title: string;
  background: string;
  estimated_minutes: number;
  version: string;
  source_type: "seeded" | "seeded_fallback" | "ai_base" | "ai_adapted" | string;
}

export const OCCUPATION_CATEGORIES = [
  "学生",
  "教育培训",
  "医疗健康",
  "互联网/信息技术",
  "工程/制造/建筑",
  "商业/金融/管理",
  "政府/公共服务",
  "科研/法律/专业服务",
  "文化/传媒/创意",
  "零售/餐饮/生活服务",
  "自由职业/个体经营",
  "待业/退休/其他",
] as const;

export type OccupationCategory = (typeof OCCUPATION_CATEGORIES)[number];

export interface StageSummary {
  stage_code: string;
  title: string;
  stage_order: number;
  context: string;
  main_question: string;
  max_followups: number;
}

export interface DialogueTurnItem {
  turn_index: number;
  speaker: "ai" | "user" | "system" | string;
  content: string;
  content_type: string;
  created_at: string;
  analysis?: Record<string, unknown> | null;
}

export interface StageProgressItem {
  stage_code: string;
  title: string;
  stage_order: number;
  status: "pending" | "active" | "completed" | string;
  max_followups: number;
  used_followups: number;
  used_clarifications: number;
  can_skip: boolean;
  skipped: boolean;
  released_dynamic_info_count: number;
  estimated_minutes: number;
  evidence_coverage?: Record<string, "complete" | "partial" | "missing" | string>;
  missing_evidence?: string[];
  waiting_for_stage_choice?: boolean;
}

export interface SessionProgress {
  total_stages: number;
  current_stage_order: number | null;
  estimated_minutes: number;
  elapsed_seconds: number | null;
  stages: StageProgressItem[];
}

export interface SessionResponse {
  session_uuid: string;
  status: string;
  flow_version?: "legacy_v2" | "progressive_v3" | "progressive_v3_2" | "progressive_v3_3";
  interviewer_style_version?: string;
  participant_nickname: string;
  scenario: ScenarioSummary;
  current_stage: StageSummary | null;
  turns: DialogueTurnItem[];
  progress?: SessionProgress | null;
  interview_progress?: {
    formal_answer_count: number;
    target_min_answers: number;
    target_max_answers: number;
    percent: number;
    estimated_remaining_minutes: number;
    elapsed_seconds?: number;
  } | null;
  language_mode: "standard" | "plain";
  phase: "onboarding" | "scenario_preparing" | "opening_pending" | "assessment" | "completed";
  onboarding?: {
    question_count: number;
    max_questions: number;
    completed: boolean;
  } | null;
  scenario_preparation?: {
    status: string;
    cache_hit: boolean;
    fallback_used: boolean;
    message?: string | null;
  } | null;
}

export interface CreateSessionPayload {
  nickname: string;
  occupation_category: OccupationCategory;
  occupation: string;
  info_collect_method?: string;
  assessment_mode?: string;
  consent_accepted: true;
  consent_version: "critical_thinking_assessment_consent_v1";
}

export interface PreparationResponse {
  session_uuid: string;
  phase: SessionResponse["phase"];
  onboarding: NonNullable<SessionResponse["onboarding"]>;
  scenario_preparation: NonNullable<SessionResponse["scenario_preparation"]>;
  assessment_ready: boolean;
}

export interface SubmitTurnPayload {
  content: string;
  content_type?: string;
  client_turn_id: string;
  answer_duration_ms?: number | null;
}

export interface SubmitTurnResponse {
  session_uuid: string;
  saved_turn_index: number;
  next_action: string;
  message: string;
  replayed?: boolean;
}

export interface FinishSessionResponse {
  session_uuid: string;
  status: string;
  completed_at: string;
}

export interface SkipStageResponse {
  session_uuid: string;
  next_action: string;
  message: string;
}

export interface ContinueStageResponse extends SkipStageResponse {}

export interface LanguageModeResponse {
  session_uuid: string;
  language_mode: "standard" | "plain";
}

export interface SubmitFeedbackPayload {
  realism_score: number;
  difficulty_score: number;
  naturalness_score: number;
  fatigue_score: number;
  report_trust_score: number;
  overall_satisfaction_score: number;
  open_feedback?: string | null;
}

export interface FeedbackResponse extends SubmitFeedbackPayload {
  session_uuid: string;
  submitted_at: string;
}

export interface FeedbackStateResponse {
  session_uuid: string;
  submitted: boolean;
  feedback: FeedbackResponse | null;
}

export interface AssessmentReportResponse {
  session_uuid: string;
  status: string;
  report: ReportOutput;
}
