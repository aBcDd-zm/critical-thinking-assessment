from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext, DimensionScore, ScoringOutput


_COVERAGE_RANK = {
    "missing": 0,
    "partial": 1,
    "covered": 2,
}

_MULTI_PERSPECTIVE_SCORE_FLOOR_KEYS = {
    "利益相关方",
    "视角冲突",
    "取舍依据",
}


def apply_semantic_evidence_guardrails(
    context: AgentRuntimeContext,
    output: ScoringOutput,
) -> ScoringOutput:
    observation_stages_by_dimension: dict[str, set[str]] = {}
    primary_stages_by_dimension: dict[str, set[str]] = {}

    for binding in context.stage_dimension_bindings:
        observation_stages_by_dimension.setdefault(
            binding.dimension_key,
            set(),
        ).add(binding.stage_code)

        if binding.observe_role == "primary":
            primary_stages_by_dimension.setdefault(
                binding.dimension_key,
                set(),
            ).add(binding.stage_code)

    snapshots = _merged_stage_evidence_snapshots(context)
    scores: list[DimensionScore] = []
    warnings = list(output.warnings)
    gaps = list(output.detected_score_gaps)

    for score in output.scores:
        observed_stage_codes = observation_stages_by_dimension.get(
            score.dimension_key,
            set(),
        )
        primary_stage_codes = primary_stages_by_dimension.get(
            score.dimension_key,
            set(),
        )

        observed_snapshots = [
            snapshots[stage_code]
            for stage_code in observed_stage_codes
            if stage_code in snapshots
        ]
        primary_snapshots = [
            snapshots[stage_code]
            for stage_code in primary_stage_codes
            if stage_code in snapshots
        ]

        if not observed_snapshots:
            scores.append(score)
            continue

        coverage_states = _coverage_states(observed_snapshots)
        primary_coverage_states = _coverage_states(primary_snapshots)

        covered_count = coverage_states.count("covered")
        primary_covered_count = primary_coverage_states.count("covered")
        total_count = len(coverage_states)

        has_supporting_evidence = any(
            item.evidence_type == "supporting_evidence"
            for item in score.evidence
        )

        if covered_count == 0:
            if (
                score.assessment_status == "scored"
                and has_supporting_evidence
            ):
                adjusted_score = _cap_confidence(score, 0.55)
                scores.append(adjusted_score)
                warnings.append(
                    f"semantic_snapshot_incomplete:{score.dimension_key}"
                )
                continue

            scores.append(
                score.model_copy(
                    update={
                        "score": None,
                        "assessment_status": "insufficient_evidence",
                        "confidence": None,
                        "reason": (
                            "本次对话在与该维度相关的观察阶段中，"
                            "尚未形成可追溯的 covered 语义证据，"
                            "因此暂标记为 IE。"
                        ),
                        "evidence": [],
                    }
                )
            )
            warnings.append(
                f"semantic evidence guardrail marked "
                f"{score.dimension_key} as IE: "
                "no covered evidence across observed stages "
                "and no supporting evidence in scoring output"
            )
            gaps.append(
                f"本次对话尚未充分呈现 "
                f"{score.dimension_key} 的可追溯证据。"
            )
            continue

        if score.assessment_status != "scored":
            scores.append(score)
            warnings.append(
                f"covered_semantic_evidence_but_unscored:"
                f"{score.dimension_key}"
            )
            continue

        covered_evidence_keys = _covered_evidence_keys(
            observed_snapshots
        )
        if (
            score.dimension_key == "multiple_perspectives"
            and score.score is not None
            and score.score < 3
            and _MULTI_PERSPECTIVE_SCORE_FLOOR_KEYS
            <= covered_evidence_keys
        ):
            score = score.model_copy(
                update={
                    "score": 3,
                    "reason": (
                        "本次对话已覆盖利益相关方、视角冲突和取舍依据，"
                        "至少达到多元视角的中等锚点。"
                    ),
                }
            )
            warnings.append(
                "semantic_score_floor_applied:"
                "multiple_perspectives"
            )

        confidence_caps: list[float] = []

        if primary_stage_codes and primary_covered_count == 0:
            confidence_caps.append(0.65)
            warnings.append(
                f"evidence_outside_primary_stage:"
                f"{score.dimension_key}"
            )

        if total_count > covered_count:
            coverage_confidence_cap = round(
                min(
                    0.85,
                    0.4 + 0.45 * (covered_count / total_count),
                ),
                2,
            )
            confidence_caps.append(coverage_confidence_cap)
            warnings.append(
                f"semantic evidence guardrail found partial coverage "
                f"for {score.dimension_key}: "
                f"{covered_count}/{total_count} evidence items covered"
            )

        if confidence_caps:
            score = _cap_confidence(score, min(confidence_caps))

        scores.append(score)

    return output.model_copy(
        update={
            "scores": scores,
            "warnings": _deduplicate(warnings),
            "detected_score_gaps": _deduplicate(gaps),
        }
    )


def _merged_stage_evidence_snapshots(
    context: AgentRuntimeContext,
) -> dict[str, list[dict]]:
    snapshots_by_stage: dict[str, dict[str, dict]] = {}

    for turn in context.dialogue_history:
        if turn.speaker != "user" or not turn.stage_code:
            continue

        analysis = turn.analysis_json or {}
        snapshot = analysis.get("resolved_evidence_snapshot")
        if not isinstance(snapshot, list):
            continue

        stage_items = snapshots_by_stage.setdefault(
            turn.stage_code,
            {},
        )

        for item in snapshot:
            if not isinstance(item, dict):
                continue

            evidence_key = str(item.get("evidence_key") or "").strip()
            coverage = item.get("coverage")

            if (
                not evidence_key
                or coverage not in _COVERAGE_RANK
            ):
                continue

            existing = stage_items.get(evidence_key)
            if existing is None:
                stage_items[evidence_key] = dict(item)
                continue

            stage_items[evidence_key] = _merge_snapshot_item(
                existing,
                item,
            )

    return {
        stage_code: list(items_by_key.values())
        for stage_code, items_by_key in snapshots_by_stage.items()
    }


def _merge_snapshot_item(
    existing: dict,
    incoming: dict,
) -> dict:
    existing_coverage = str(existing.get("coverage") or "missing")
    incoming_coverage = str(incoming.get("coverage") or "missing")

    if (
        _COVERAGE_RANK.get(incoming_coverage, -1)
        >= _COVERAGE_RANK.get(existing_coverage, -1)
    ):
        selected = dict(incoming)
    else:
        selected = dict(existing)

    supporting_indexes: list[int] = []
    for source in (existing, incoming):
        indexes = source.get("supporting_turn_indexes")
        if not isinstance(indexes, list):
            continue

        for index in indexes:
            if (
                isinstance(index, int)
                and index not in supporting_indexes
            ):
                supporting_indexes.append(index)

    selected["supporting_turn_indexes"] = supporting_indexes
    return selected


def _covered_evidence_keys(
    snapshots: list[list[dict]],
) -> set[str]:
    return {
        str(item.get("evidence_key") or "").strip()
        for snapshot in snapshots
        for item in snapshot
        if isinstance(item, dict)
        and item.get("coverage") == "covered"
        and str(item.get("evidence_key") or "").strip()
    }


def _coverage_states(
    snapshots: list[list[dict]],
) -> list[str]:
    return [
        str(item.get("coverage"))
        for snapshot in snapshots
        for item in snapshot
        if isinstance(item, dict)
        and item.get("coverage") in _COVERAGE_RANK
    ]


def _cap_confidence(
    score: DimensionScore,
    confidence_cap: float,
) -> DimensionScore:
    current_confidence = score.confidence
    adjusted_confidence = (
        confidence_cap
        if current_confidence is None
        else min(current_confidence, confidence_cap)
    )
    return score.model_copy(
        update={"confidence": adjusted_confidence}
    )


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


__all__ = ["apply_semantic_evidence_guardrails"]
