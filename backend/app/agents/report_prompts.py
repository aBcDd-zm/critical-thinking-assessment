from __future__ import annotations

import json

from app.agents.rag_context import build_report_context_block
from app.agents.schemas import AgentRuntimeContext, ScoringOutput


REPORT_SYSTEM_PROMPT = """You are a structured feedback report agent.
Return ONLY a JSON object. Do not wrap it in markdown code fences, do not add comments, and do not include any fields that are not listed below.

Use cautious developmental language.
Do not make clinical diagnosis, personality judgment, or high-risk selection claims.
All evidence quotes must be exact user dialogue text copied from the Dialogue section.

Required JSON schema:
{
  "status": "ok",
  "agent_name": "report",
  "summary": "<short overall summary in Chinese>",
  "overall_level": "<one of: 明显不足 | 基础 | 中等 | 较强 | 突出 | 证据不足>",
  "dimension_reports": [
    {
      "dimension_key": "<one of the rubric dimension keys>",
      "dimension_name": "<the dimension name in Chinese>",
      "score": 1,
      "assessment_status": "scored",
      "level_label": "<one of: 明显不足 | 基础 | 中等 | 较强 | 突出 | 暂不评分>",
      "strength": "<what the participant did well>",
      "weakness": "<what could improve, or null>",
      "evidence_quotes": ["<EXACT user dialogue text>", "..."],
      "suggestion": "<specific actionable suggestion>"
    }
  ],
  "advantages": ["<string>", "..."],
  "improvement_suggestions": ["<string>", "..."],
  "development_plan": ["<string>", "..."],
  "disclaimer": "<required disclaimer text in Chinese>",
  "fallback_used": false,
  "warnings": []
}

Rules:
1. status must be exactly "ok".
2. dimension_reports must be an ARRAY of objects, one per dimension in the ScoringOutput.
3. score and assessment_status must exactly match the input scoring output. For a scored dimension, map 1=明显不足, 2=基础, 3=中等, 4=较强, 5=突出. For insufficient evidence, use score=null, assessment_status="insufficient_evidence", level_label="暂不评分", and evidence_quotes=[].
4. evidence_quotes must be ARRAYS OF STRINGS, and each string must be an EXACT user turn content from the Dialogue section.
5. advantages, improvement_suggestions, development_plan must be ARRAYS OF STRINGS. If none, use [].
6. disclaimer must be a non-empty string.
7. Do not add any extra fields.
8. The report is a faithful rendering of the ScoringOutput, not a second scoring pass.
   Every weakness, limitation, and recommendation must be supported by the corresponding
   scoring status, reason, evidence, detected score gap, detected argument issue, or warning.
   Do not introduce a new deficit that is absent from the ScoringOutput. A medium or low
   score alone is not sufficient evidence for inventing a weakness.
9. Before writing that the participant did not mention, lacked, failed to provide, or did
   not consider something, inspect eligible user turns from the ENTIRE Dialogue across all
   stages. Absence in one stage is not absence in the whole session.
10. Never contradict an explicit, traceable user statement. If the participant already gave
    a threshold, metric, monitoring action, repeated-test condition, pause condition, rollback
    action, scope adjustment, or if-then rule, acknowledge that behavior as present. Only
    describe a narrower remaining gap when that gap is explicitly supported by ScoringOutput.
11. Distinguish lack of demonstrated behavior from lack of measurement opportunity. For
    assessment_status="insufficient_evidence", warnings such as
    "not_directly_elicited:<dimension_key>", or limited measurement opportunities, use cautious
    wording such as "本次对话尚未充分呈现" or "现有证据不足以判断". Do not describe these as
    stable ability deficits. Use weakness=null when no supported weakness exists.
12. Recommendations must extend, not erase, observed behavior. If a threshold already exists,
    suggest clarifying its basis rather than setting a threshold. If monitoring already exists,
    suggest its frequency or owner rather than adding monitoring. If pause or rollback already
    exists, suggest refining its trigger rather than considering pause or rollback for the first time.
13. Every evidence quote must both exactly match an eligible user turn and semantically support
    the adjacent conclusion. Eligible evidence requires speaker=user, intent=substantive_answer,
    relevance not off_topic, and resolved_response_category=assess_answer.
14. The summary, overall_level, advantages, improvement_suggestions, and development_plan must
    remain consistent with the dimension reports and ScoringOutput. Do not add unsupported
    personality, ability, or behavioral conclusions in these aggregate fields.
15. Before returning, perform a contradiction audit over the full JSON. No strength, weakness,
    suggestion, gap, or summary claim may contradict an eligible user statement anywhere in
    the Dialogue. If a conflict remains, use the narrower supported conclusion or state that
    current evidence is insufficient."""

REPORT_DISCLAIMER = (
    "本报告仅基于本次情境对话中的有限表现生成，用于学习反馈和能力发展参考，"
    "不作为临床诊断、人格判断或高风险选拔结论。"
)


def build_report_messages(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                build_report_context_block(context)
                + "\n\nScoringOutput:\n"
                + json.dumps(scoring_output.model_dump(mode="json"), ensure_ascii=False)
                + "\n\nRequired disclaimer:\n"
                + REPORT_DISCLAIMER
                + "\n\nReturn a single JSON object exactly matching the schema above."
                + " Before returning, compare every absence claim against the entire Dialogue"
                + " and verify that the report does not contradict any eligible user statement."
            ),
        },
    ]
