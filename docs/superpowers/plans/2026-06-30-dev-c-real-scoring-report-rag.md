# DEV-C Real Scoring Report RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Full-stack C from a mock keyword baseline into a production-style scoring and report pipeline that can use rubric/RAG context, DeepSeek JSON mode, robust fallbacks, database persistence, and the existing report frontend.

**Architecture:** Keep the current mock path as the offline fallback, and add a real scoring/report path behind the existing model gateway. Scoring and report agents will build structured prompts from `AgentRuntimeContext`, parse and validate JSON into existing `ScoringOutput` and `ReportOutput`, persist through existing services, and expose results through the current A-owned session report API.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, MySQL, DeepSeek through `ModelGatewayService`, Vue 3 + Vite + TypeScript for report display, no new runtime service unless explicitly approved.

## Global Constraints

- Keep the current mock mode runnable without API keys.
- Do not add database migrations unless the existing tables cannot store the required data.
- Do not modify B-owned dialogue strategy files for C-only work.
- `ScoringOutput` and `ReportOutput` remain the contract consumed by A and the frontend.
- Evidence used in scoring must come from `dialogue_history` or released scenario/dynamic context, not invented text.
- Report copy must avoid clinical diagnosis, personality judgment, and high-risk selection claims.
- User-facing report page must never show raw debug JSON.

---

## Current Baseline

The current Full-stack C baseline is functional:

- `backend/app/agents/scoring_agent.py` delegates to `build_mock_scoring_output`.
- `backend/app/agents/report_agent.py` delegates to `build_mock_report_output`.
- `backend/app/agents/mock_scoring_report.py` provides keyword-based scoring/report generation.
- `backend/app/services/scoring_service.py` persists `ScoreSnapshot`, `ScoreResult`, and `ScoreEvidence`.
- `backend/app/services/report_service.py` persists `AssessmentReport` and report `AgentTrace`.
- `backend/app/services/session_service.py` calls scoring and report generation during `finish_session`.
- `frontend/src/views/AssessmentReportView.vue` displays the structured report.

Verified commands:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py

cd frontend
npm run build
```

## Target Capabilities

1. Real-mode `ScoringAgent` calls the model gateway with JSON mode and validates `ScoringOutput`.
2. Real-mode `ReportAgent` calls the model gateway with JSON mode and validates `ReportOutput`.
3. RAG/professional context builder assembles rubric anchors, invalid evidence rules, stage goals, dynamic information, and report boundaries.
4. Evidence validation rejects or downgrades model evidence that cannot be traced to a dialogue turn.
5. Mock fallback remains deterministic and is recorded with `fallback_used=true` and warnings when real-mode output fails.
6. Automated checks cover mock mode, malformed model output, evidence validation, report contract validation, and DB persistence.
7. The frontend continues to render final reports without raw debug JSON.

## File Map

### Backend Agent Layer

- Modify: `backend/app/agents/scoring_agent.py`
  - Selects mock or real generation mode.
  - Calls a new scoring LLM client.
  - Falls back to mock on model/parse/evidence validation failure.

- Modify: `backend/app/agents/report_agent.py`
  - Selects mock or real generation mode.
  - Calls a new report LLM client.
  - Falls back to mock on model/parse/schema validation failure.

- Create: `backend/app/agents/scoring_report_llm_client.py`
  - Shared sync wrapper over `ModelGatewayService.chat`.
  - Sends JSON-mode requests for scoring/report.
  - Extracts raw text and parses JSON.

- Modify: `backend/app/agents/rag_context.py`
  - Builds professional scoring/report context from runtime context.
  - Exposes deterministic helpers used by prompts and tests.

- Create: `backend/app/agents/scoring_report_validators.py`
  - Validates dimension coverage, score ranges, evidence traceability, and report completeness.

- Modify: `backend/app/agents/scoring_prompts.py`
  - Defines scoring system/user messages with strict JSON schema instructions.

- Modify: `backend/app/agents/report_prompts.py`
  - Defines report system/user messages with disclaimer and user-safe copy rules.

### Backend Services and Scripts

- Modify: `backend/app/services/session_service.py`
  - Preserve current finish integration.
  - Record real/mock generation mode and fallback warnings through existing output/trace objects.

- Modify: `backend/scripts/check_scoring_report_agent.py`
  - Keep existing mock fixture checks.
  - Add mocked real-client cases for parse and validation behavior.

- Create: `backend/scripts/check_scoring_report_real_strict.py`
  - Optional real DeepSeek check.
  - Requires `MODEL_GATEWAY_MODE=real` and `DEEPSEEK_API_KEY`.

- Modify: `backend/scripts/check_report_generation_flow.py`
  - Add assertions for fallback flags and trace model metadata.

### Frontend

- Modify: `frontend/src/types/report.ts`
  - Add optional `warnings` and fallback display fields only if backend contract changes.

- Modify: `frontend/src/views/AssessmentReportView.vue`
  - Keep no raw JSON display.
  - Add visible fallback warning if `report.fallback_used` or `report.warnings.length > 0`.

- Modify: `frontend/src/components/report/ReportHero.vue`
  - Display a restrained confidence/fallback note using existing warning props.

## Task 1: RAG Context Builder

**Files:**
- Modify: `backend/app/agents/rag_context.py`
- Test through: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Produces:
  - `collect_user_answer_texts(context: AgentRuntimeContext) -> list[str]`
  - `collect_professional_context(context: AgentRuntimeContext) -> list[str]`
  - `build_scoring_context_block(context: AgentRuntimeContext) -> str`
  - `build_report_context_block(context: AgentRuntimeContext) -> str`

- [ ] **Step 1: Add context block helpers**

Add deterministic text builders:

```python
def build_scoring_context_block(context: AgentRuntimeContext) -> str:
    sections = [
        f"Session: {context.session.session_uuid}",
        f"Scenario: {context.scenario.title}",
        f"Current stage: {context.stage.stage_code} - {context.stage.title}",
        "Rubric dimensions:",
    ]
    for dimension in context.rubric_dimensions:
        sections.append(
            f"- {dimension.dimension_key} | {dimension.name} | {dimension.definition} | "
            f"invalid_evidence={dimension.invalid_evidence_desc or ''}"
        )
    sections.append("Rubric anchors:")
    for anchor in context.rubric_anchors:
        examples = "; ".join(anchor.evidence_examples or [])
        counters = "; ".join(anchor.counter_examples or [])
        sections.append(
            f"- {anchor.dimension_key} level={anchor.score_level} "
            f"{anchor.level_name}: {anchor.behavior_desc}; examples={examples}; counters={counters}"
        )
    sections.append("Dialogue:")
    for turn in context.dialogue_history:
        sections.append(
            f"- turn_id={turn.turn_id} stage={turn.stage_code} speaker={turn.speaker} "
            f"type={turn.content_type}: {turn.content}"
        )
    return "\n".join(sections)
```

- [ ] **Step 2: Add report context helper**

```python
def build_report_context_block(context: AgentRuntimeContext) -> str:
    scoring_context = build_scoring_context_block(context)
    boundaries = [
        "Report boundaries:",
        "- Do not make clinical diagnosis.",
        "- Do not infer personality or mental illness.",
        "- Use cautious developmental language.",
        "- Evidence quotes must come from dialogue turns.",
    ]
    return scoring_context + "\n" + "\n".join(boundaries)
```

- [ ] **Step 3: Add script assertions**

In `check_scoring_report_agent.py`, after building one context, assert:

```python
context_block = build_scoring_context_block(context)
if "Rubric dimensions:" not in context_block:
    raise AssertionError("scoring context missing rubric dimensions")
if "Dialogue:" not in context_block:
    raise AssertionError("scoring context missing dialogue")
```

- [ ] **Step 4: Run validation**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: all three scoring cases pass.

## Task 2: Scoring and Report Validators

**Files:**
- Create: `backend/app/agents/scoring_report_validators.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Produces:
  - `validate_scoring_output(context: AgentRuntimeContext, output: ScoringOutput) -> ScoringOutput`
  - `validate_report_output(context: AgentRuntimeContext, scoring_output: ScoringOutput, output: ReportOutput) -> ReportOutput`

- [ ] **Step 1: Create validator module**

Create `backend/app/agents/scoring_report_validators.py`:

```python
from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput


ALLOWED_EVIDENCE_TYPES = {
    "supporting_evidence",
    "weak_evidence",
    "invalid_evidence",
}


def validate_scoring_output(
    context: AgentRuntimeContext,
    output: ScoringOutput,
) -> ScoringOutput:
    expected_keys = {item.dimension_key for item in context.rubric_dimensions}
    actual_keys = {item.dimension_key for item in output.scores}
    if actual_keys != expected_keys:
        raise ValueError(
            f"Scoring dimensions mismatch: missing={sorted(expected_keys - actual_keys)}, "
            f"extra={sorted(actual_keys - expected_keys)}"
        )

    history_by_id = {
        turn.turn_id: turn.content
        for turn in context.dialogue_history
        if turn.turn_id is not None
    }
    history_texts = set(history_by_id.values())
    for score in output.scores:
        if not 1 <= score.score <= 5:
            raise ValueError(f"Score out of range for {score.dimension_key}: {score.score}")
        if score.confidence is not None and not 0 <= score.confidence <= 1:
            raise ValueError(f"Confidence out of range for {score.dimension_key}")
        for evidence in score.evidence:
            if evidence.evidence_type not in ALLOWED_EVIDENCE_TYPES:
                raise ValueError(f"Invalid evidence type: {evidence.evidence_type}")
            if evidence.text not in history_texts:
                raise ValueError(f"Evidence text is not traceable: {evidence.text}")
            if evidence.dialogue_turn_id is None:
                raise ValueError("Evidence missing dialogue_turn_id")
            if history_by_id.get(evidence.dialogue_turn_id) != evidence.text:
                raise ValueError("Evidence dialogue_turn_id does not match text")
    return output


def validate_report_output(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
    output: ReportOutput,
) -> ReportOutput:
    expected_keys = {item.dimension_key for item in scoring_output.scores}
    actual_keys = {item.dimension_key for item in output.dimension_reports}
    if actual_keys != expected_keys:
        raise ValueError(
            f"Report dimensions mismatch: missing={sorted(expected_keys - actual_keys)}, "
            f"extra={sorted(actual_keys - expected_keys)}"
        )
    if not output.disclaimer:
        raise ValueError("Report missing disclaimer")
    if not output.summary:
        raise ValueError("Report missing summary")
    history_texts = {
        turn.content
        for turn in context.dialogue_history
        if turn.speaker == "user"
    }
    for item in output.dimension_reports:
        if not 1 <= item.score <= 5:
            raise ValueError(f"Report score out of range: {item.dimension_key}")
        for quote in item.evidence_quotes:
            if quote not in history_texts:
                raise ValueError(f"Report evidence quote is not traceable: {quote}")
    return output
```

- [ ] **Step 2: Use validators in existing script**

Import and call validators after each agent output:

```python
from app.agents.scoring_report_validators import (
    validate_report_output,
    validate_scoring_output,
)

scoring_output = validate_scoring_output(context, scoring_output)
report_output = validate_report_output(context, scoring_output, report_output)
```

- [ ] **Step 3: Run validation**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: all three scoring cases pass.

## Task 3: JSON Prompt Builders

**Files:**
- Modify: `backend/app/agents/scoring_prompts.py`
- Modify: `backend/app/agents/report_prompts.py`
- Test through: `backend/scripts/check_agent_contract.py`

**Interfaces:**
- Produces:
  - `build_scoring_messages(context: AgentRuntimeContext) -> list[dict[str, str]]`
  - `build_report_messages(context: AgentRuntimeContext, scoring_output: ScoringOutput) -> list[dict[str, str]]`

- [ ] **Step 1: Add scoring messages**

In `scoring_prompts.py`, add:

```python
from app.agents.rag_context import build_scoring_context_block
from app.agents.schemas import AgentRuntimeContext


SCORING_SYSTEM_PROMPT = """You are a structured critical-thinking assessment scoring agent.
Return only a JSON object matching ScoringOutput.
Score every rubric dimension from 1 to 5.
Every evidence item must quote an exact user dialogue turn and include dialogue_turn_id.
If evidence is weak or invalid, mark evidence_type as weak_evidence or invalid_evidence.
Do not invent evidence."""


def build_scoring_messages(context: AgentRuntimeContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SCORING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                build_scoring_context_block(context)
                + "\n\nReturn JSON with keys: status, agent_name, snapshot_type, summary, "
                "trend_analysis, scores, detected_score_gaps, detected_argument_issues, "
                "fallback_used, warnings."
            ),
        },
    ]
```

- [ ] **Step 2: Add report messages**

In `report_prompts.py`, keep `REPORT_DISCLAIMER` and add:

```python
import json

from app.agents.rag_context import build_report_context_block
from app.agents.schemas import AgentRuntimeContext, ScoringOutput


REPORT_SYSTEM_PROMPT = """You are a structured feedback report agent.
Return only a JSON object matching ReportOutput.
Use cautious developmental language.
Do not make clinical diagnosis, personality judgment, or high-risk selection claims.
Evidence quotes must be exact user dialogue text."""


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
                + "\n\nReturn JSON with keys: status, agent_name, summary, overall_level, "
                "dimension_reports, advantages, improvement_suggestions, development_plan, "
                "disclaimer, fallback_used, warnings."
            ),
        },
    ]
```

- [ ] **Step 3: Run contract validation**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
```

Expected: contract check passes.

## Task 4: Shared LLM Client for C

**Files:**
- Create: `backend/app/agents/scoring_report_llm_client.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Produces:
  - `ScoringReportLLMClient.call_scoring(context: AgentRuntimeContext) -> CLLMResult[ScoringOutput]`
  - `ScoringReportLLMClient.call_report(context: AgentRuntimeContext, scoring_output: ScoringOutput) -> CLLMResult[ReportOutput]`

- [ ] **Step 1: Create client and result type**

Create:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.report_prompts import build_report_messages
from app.agents.scoring_prompts import build_scoring_messages
from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService


T = TypeVar("T", bound=BaseModel)


@dataclass
class CLLMResult(Generic[T]):
    success: bool
    output: T | None
    raw_output: str
    error_code: str | None
    error_reason: str | None
    model_name: str | None


class ScoringReportLLMClient:
    def __init__(
        self,
        model_gateway_service: ModelGatewayService | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2400,
        thinking_enabled: bool = False,
        reasoning_effort: str = "low",
    ) -> None:
        self.model_gateway_service = model_gateway_service or ModelGatewayService(get_settings())
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort

    def call_scoring(self, context: AgentRuntimeContext) -> CLLMResult[ScoringOutput]:
        return self._call(build_scoring_messages(context), ScoringOutput)

    def call_report(
        self,
        context: AgentRuntimeContext,
        scoring_output: ScoringOutput,
    ) -> CLLMResult[ReportOutput]:
        return self._call(build_report_messages(context, scoring_output), ReportOutput)

    def _call(self, messages: list[dict[str, str]], output_model: type[T]) -> CLLMResult[T]:
        try:
            response = asyncio.run(
                self.model_gateway_service.chat(
                    ModelChatRequest(
                        messages=[ChatMessage(**message) for message in messages],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        json_mode=True,
                        thinking_enabled=self.thinking_enabled,
                        reasoning_effort=self.reasoning_effort,
                    )
                )
            )
        except Exception as exc:
            return CLLMResult(False, None, "", "MODEL_GATEWAY_ERROR", str(exc), None)

        raw_output = response.content or ""
        payload = _extract_json_object(raw_output)
        if payload is None:
            return CLLMResult(False, None, raw_output, "INVALID_JSON", "No JSON object found", response.model)
        try:
            output = output_model.model_validate(payload)
        except ValidationError as exc:
            return CLLMResult(False, None, raw_output, "SCHEMA_VALIDATION_ERROR", str(exc), response.model)
        return CLLMResult(True, output, raw_output, None, None, response.model)


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    try:
        loaded = json.loads(stripped)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    loaded = json.loads(stripped[start : index + 1])
                    return loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
```

- [ ] **Step 2: Add client parse checks in script**

Use `_extract_json_object` indirectly by mocking `ModelGatewayService.chat` in a small in-script helper if no pytest suite is added. At minimum, instantiate the client in mock mode only after Task 5 integrates fallback.

- [ ] **Step 3: Run contract validation**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
```

Expected: pass.

## Task 5: Real ScoringAgent with Mock Fallback

**Files:**
- Modify: `backend/app/agents/scoring_agent.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Consumes:
  - `ScoringReportLLMClient.call_scoring`
  - `validate_scoring_output`
  - `build_mock_scoring_output`

- [ ] **Step 1: Replace direct mock-only implementation**

Update `ScoringAgent`:

```python
from __future__ import annotations

from typing import Literal

from app.agents.mock_scoring_report import build_mock_scoring_output
from app.agents.schemas import AgentRuntimeContext, ScoringOutput
from app.agents.scoring_report_llm_client import ScoringReportLLMClient
from app.agents.scoring_report_validators import validate_scoring_output
from app.core.config import get_settings


class ScoringAgent:
    def __init__(self, llm_client: ScoringReportLLMClient | None = None) -> None:
        self.llm_client = llm_client or ScoringReportLLMClient()

    def generate(
        self,
        context: AgentRuntimeContext,
        snapshot_type: Literal["turn", "stage", "final"] = "final",
    ) -> ScoringOutput:
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return build_mock_scoring_output(context, snapshot_type=snapshot_type)

        result = self.llm_client.call_scoring(context)
        if result.success and result.output is not None:
            try:
                output = result.output.model_copy(update={"snapshot_type": snapshot_type})
                return validate_scoring_output(context, output)
            except Exception as exc:
                return _fallback(context, snapshot_type, f"VALIDATION_ERROR: {exc}")
        return _fallback(
            context,
            snapshot_type,
            f"{result.error_code or 'MODEL_ERROR'}: {result.error_reason or 'unknown error'}",
        )


def _fallback(
    context: AgentRuntimeContext,
    snapshot_type: Literal["turn", "stage", "final"],
    warning: str,
) -> ScoringOutput:
    output = build_mock_scoring_output(context, snapshot_type=snapshot_type)
    return output.model_copy(
        update={
            "fallback_used": True,
            "warnings": output.warnings + [warning],
        }
    )
```

- [ ] **Step 2: Add malformed real-output test path**

In `check_scoring_report_agent.py`, add a fake client:

```python
class FakeBadScoringClient:
    def call_scoring(self, context):
        from app.agents.scoring_report_llm_client import CLLMResult
        return CLLMResult(False, None, "{bad", "INVALID_JSON", "bad json", "fake")

fallback_output = ScoringAgent(llm_client=FakeBadScoringClient()).generate(context)
if not fallback_output.fallback_used:
    raise AssertionError("Expected scoring fallback_used=true for bad real output")
```

This check must run with real-mode simulation. If direct settings injection is too invasive, create a separate unit helper in Task 8.

- [ ] **Step 3: Run validations**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: both pass.

## Task 6: Real ReportAgent with Mock Fallback

**Files:**
- Modify: `backend/app/agents/report_agent.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Consumes:
  - `ScoringReportLLMClient.call_report`
  - `validate_report_output`
  - `build_mock_report_output`

- [ ] **Step 1: Replace direct mock-only implementation**

Update `ReportAgent`:

```python
from __future__ import annotations

from app.agents.mock_scoring_report import build_mock_report_output
from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput
from app.agents.scoring_report_llm_client import ScoringReportLLMClient
from app.agents.scoring_report_validators import validate_report_output
from app.core.config import get_settings


class ReportAgent:
    def __init__(self, llm_client: ScoringReportLLMClient | None = None) -> None:
        self.llm_client = llm_client or ScoringReportLLMClient()

    def generate(
        self,
        context: AgentRuntimeContext,
        scoring_output: ScoringOutput,
    ) -> ReportOutput:
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return build_mock_report_output(context, scoring_output)

        result = self.llm_client.call_report(context, scoring_output)
        if result.success and result.output is not None:
            try:
                return validate_report_output(context, scoring_output, result.output)
            except Exception as exc:
                return _fallback(context, scoring_output, f"VALIDATION_ERROR: {exc}")
        return _fallback(
            context,
            scoring_output,
            f"{result.error_code or 'MODEL_ERROR'}: {result.error_reason or 'unknown error'}",
        )


def _fallback(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
    warning: str,
) -> ReportOutput:
    output = build_mock_report_output(context, scoring_output)
    return output.model_copy(
        update={
            "fallback_used": True,
            "warnings": output.warnings + [warning],
        }
    )
```

- [ ] **Step 2: Add fallback assertion**

Add fake bad report client assertion:

```python
class FakeBadReportClient:
    def call_report(self, context, scoring_output):
        from app.agents.scoring_report_llm_client import CLLMResult
        return CLLMResult(False, None, "{bad", "INVALID_JSON", "bad json", "fake")

fallback_report = ReportAgent(llm_client=FakeBadReportClient()).generate(context, scoring_output)
if not fallback_report.fallback_used:
    raise AssertionError("Expected report fallback_used=true for bad real output")
```

- [ ] **Step 3: Run validations**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: both pass.

## Task 7: Persistence Metadata and Frontend Fallback Notice

**Files:**
- Modify: `backend/app/services/scoring_service.py`
- Modify: `backend/app/services/report_service.py`
- Modify: `frontend/src/components/report/ReportHero.vue`
- Modify: `frontend/src/views/AssessmentReportView.vue`

**Interfaces:**
- Produces user-visible fallback note based on existing `ReportOutput.fallback_used` and `warnings`.

- [ ] **Step 1: Preserve output mode metadata in traces**

In `ScoringService.persist_scoring_output`, change `config_snapshot_json`:

```python
config_snapshot_json={
    "snapshot_type": output.snapshot_type,
    "fallback_used": output.fallback_used,
    "warnings": output.warnings,
},
```

In `ReportService.persist_report_output`, change `config_snapshot_json`:

```python
config_snapshot_json={
    "report_version": "v2",
    "fallback_used": output.fallback_used,
    "warnings": output.warnings,
},
```

- [ ] **Step 2: Show fallback note in report hero**

In `ReportHero.vue`, make `hasLowConfidence` also check explicit warnings and fallback:

```ts
const hasLowConfidence = computed(() =>
  (props.warnings || []).some(
    (warning) =>
      warning.includes("limited_evidence") ||
      warning.includes("low_confidence") ||
      warning.includes("VALIDATION_ERROR") ||
      warning.includes("MODEL_ERROR"),
  ),
);
```

- [ ] **Step 3: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: build succeeds.

## Task 8: Strict Real-Mode Check Script

**Files:**
- Create: `backend/scripts/check_scoring_report_real_strict.py`

**Interfaces:**
- Produces a manual real-mode acceptance command.

- [ ] **Step 1: Create script**

Create script:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents import ReportOutput, ScoringOutput
from app.agents.report_agent import ReportAgent
from app.agents.scoring_agent import ScoringAgent
from app.core.config import get_settings
from scripts.check_scoring_report_agent import (
    build_scoring_context,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)


def main() -> int:
    settings = get_settings()
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise AssertionError("MODEL_GATEWAY_MODE must be real for strict real check.")
    if not settings.DEEPSEEK_API_KEY:
        raise AssertionError("DEEPSEEK_API_KEY is required for strict real check.")

    users, dialogue_cases, scoring_cases = load_fixtures()
    scenario_seed, rubric_seed = load_seed_data()
    users_by_id = validate_users(users)
    dialogue_cases_by_id = validate_dialogue_cases(
        dialogue_cases,
        users_by_id,
        scenario_seed,
        rubric_seed,
    )
    validate_scoring_cases(scoring_cases, users_by_id, dialogue_cases_by_id, rubric_seed)

    scoring_case = next(case for case in scoring_cases if case["case_id"] == "workplace_strong_scoring")
    context = build_scoring_context(
        scoring_case,
        users_by_id,
        dialogue_cases_by_id,
        scenario_seed,
        rubric_seed,
    )
    scoring_output = ScoringOutput.model_validate(
        ScoringAgent().generate(context, snapshot_type="final").model_dump()
    )
    if scoring_output.fallback_used:
        raise AssertionError(f"Real scoring fell back: {scoring_output.warnings}")
    report_output = ReportOutput.model_validate(
        ReportAgent().generate(context, scoring_output).model_dump()
    )
    if report_output.fallback_used:
        raise AssertionError(f"Real report fell back: {report_output.warnings}")
    print("Strict real scoring/report check passed.")
    print(f"score_count={len(scoring_output.scores)}")
    print(f"dimension_reports={len(report_output.dimension_reports)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run mock checks**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: pass.

- [ ] **Step 3: Run strict real check only when key exists**

Run:

```powershell
cd backend
$env:MODEL_GATEWAY_MODE='real'
$env:DEEPSEEK_API_KEY='<your-key>'
.\.venv\Scripts\python.exe scripts\check_scoring_report_real_strict.py
```

Expected: strict real check passes without fallback.

## Task 9: End-to-End Finish Flow

**Files:**
- Modify: `backend/scripts/check_report_generation_flow.py`
- No production code change unless the script exposes a real bug.

**Interfaces:**
- Confirms A/C integration after `finish_session`.

- [ ] **Step 1: Add final report API shape assertion**

After `response = SessionService(db).get_report(session.session_uuid)`, assert:

```python
required_report_keys = {
    "summary",
    "overall_level",
    "dimension_reports",
    "advantages",
    "improvement_suggestions",
    "development_plan",
    "disclaimer",
}
missing = required_report_keys - set(response.report)
if missing:
    raise AssertionError(f"Report API missing keys: {sorted(missing)}")
if len(response.report["dimension_reports"]) != 6:
    raise AssertionError("Report API should return six dimension reports")
```

- [ ] **Step 2: Run flow check**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: report generation DB flow passes and cleanup is done.

## Task 10: Final Verification Matrix

**Files:**
- No code changes.

- [ ] **Step 1: Backend contract check**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
```

Expected: `Agent contract check passed`.

- [ ] **Step 2: C agent checks**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: weak, medium, and strong cases pass.

- [ ] **Step 3: DB flow check**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: score count is 6, report status is generated.

- [ ] **Step 4: Full backend smoke check**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_mock_assessment_flow.py
```

Expected: mock assessment flow passes.

- [ ] **Step 5: Frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: `vue-tsc --noEmit` and Vite build pass.

- [ ] **Step 6: Browser manual acceptance**

1. Open `http://localhost:5173/assessment`.
2. Complete or use a generated session.
3. Open `/assessment/report/{sessionUuid}`.
4. Confirm six dimensions render.
5. Confirm no raw JSON/debug section is visible, including with `?debug=1`.
6. Confirm disclaimer is visible.

## Acceptance Criteria

- Mock mode remains fully operational without API keys.
- Real mode can generate `ScoringOutput` and `ReportOutput` through the model gateway.
- Invalid or untraceable real output falls back to mock and records warnings.
- Every score has valid dimension key, 1-5 score, reason, confidence, and traceable evidence where evidence exists.
- Low-information answers produce low scores or low confidence and invalid/weak evidence.
- Report contains summary, six dimension reports, advantages, improvement suggestions, development plan, and disclaimer.
- Database contains scoring snapshots, results, evidence, report row, and agent traces.
- Frontend report page displays structured report only, no raw JSON.

## Risks and Mitigations

- **DeepSeek returns malformed JSON:** JSON extraction, schema validation, and mock fallback prevent finish flow failure.
- **Model invents evidence:** validator rejects untraceable evidence and triggers fallback.
- **Prompt too long:** context builders are deterministic; if token pressure appears, truncate dialogue history by stage while preserving latest user answers.
- **Mock and real outputs diverge:** both must validate against the same Pydantic schema and validators.
- **Professional validity remains limited:** add psychology-team review of rubric anchors and sampled reports before public claims.

## Suggested Schedule

- Day 1: Tasks 1-3, context and prompt builders.
- Day 2: Tasks 4-6, real model clients and fallback behavior.
- Day 3: Tasks 7-9, trace metadata, frontend fallback note, DB/API integration checks.
- Day 4: Task 10, real-mode smoke test, manual review, and documentation cleanup.

## PR Split

1. `DEV-C-REAL-001 add scoring/report context builders and validators`
2. `DEV-C-REAL-002 add scoring/report model client and prompts`
3. `DEV-C-REAL-003 integrate real scoring/report agents with fallback`
4. `DEV-C-REAL-004 harden report persistence and frontend warnings`
5. `DEV-C-REAL-005 add strict real-mode validation script`

## Completion Report Template

Use this in the PR description:

```markdown
## What changed
- Added real-mode scoring/report generation through model gateway.
- Added evidence/report validators.
- Preserved mock fallback.

## Validation
- `python scripts/check_agent_contract.py`
- `python scripts/check_scoring_report_agent.py`
- `python scripts/check_report_generation_flow.py`
- `python scripts/check_mock_assessment_flow.py`
- `npm run build`

## Known limits
- Real-mode quality still depends on prompt and rubric review.
- Strict real check requires `DEEPSEEK_API_KEY`.
```
