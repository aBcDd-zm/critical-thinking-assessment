export interface DimensionReport {
  dimension_key: string;
  dimension_name: string;
  score: number | null;
  assessment_status: "scored" | "insufficient_evidence" | string;
  level_label: string;
  strength: string;
  weakness: string | null;
  evidence_quotes: string[];
  suggestion: string;
  evidence_sufficiency_index?: number | null;
  evidence_sufficiency_level?: "low" | "medium" | "high" | null;
  score_kind?: "supported" | "provisional" | "unobserved";
  evidence_sufficiency_note?: string;
}

export interface MeasurementQuality {
  status: "valid" | "caution" | "invalid";
  technical_failure_rate: number;
  total_fallback_rate: number;
  missing_events: string[];
  unobserved_dimensions?: string[];
  provisional_dimensions?: string[];
  scoring_contamination_turn_ids?: number[];
  retest_recommended: boolean;
  reasons: string[];
  overall_evidence_sufficiency_index: number | null;
}

export interface ReportOutput {
  status: "ok";
  agent_name: "report";
  summary: string;
  overall_level: string;
  dimension_reports: DimensionReport[];
  advantages: string[];
  improvement_suggestions: string[];
  development_plan: string[];
  disclaimer: string;
  fallback_used: boolean;
  warnings: string[];
  measurement_quality?: MeasurementQuality;
}
