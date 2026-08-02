export interface EvidenceItem {
  text: string;
  evidence_type: "supporting_evidence" | "weak_evidence" | "invalid_evidence" | string;
  explanation: string | null;
  dialogue_turn_id: number | null;
}

export interface DimensionScore {
  dimension_key: string;
  score: number | null;
  assessment_status: "scored" | "insufficient_evidence" | string;
  confidence: number | null;
  reason: string;
  evidence: EvidenceItem[];
  scoring_source: "agent" | "mock" | "manual" | string;
}

export interface ScoringOutput {
  status: "ok";
  agent_name: "scoring";
  snapshot_type: "turn" | "stage" | "final";
  summary: string;
  trend_analysis: string | null;
  scores: DimensionScore[];
  detected_score_gaps: string[];
  detected_argument_issues: string[];
  fallback_used: boolean;
  warnings: string[];
}
