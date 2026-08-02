#!/usr/bin/env python3
"""Analyze Humanistic Interviewer blind-review agreement after generation.

This descriptive analyzer is deliberately separate from the frozen release
evaluator. It does not use the arm key, change release gates, or decide whether
the Humanistic style may be enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


ANALYZER_ID = "humanistic_inter_rater_agreement_v1"
SCORE_FIELDS = ("naturalness", "warmth", "clarity")
PASS_FIELDS = (
    "faithfulness_pass",
    "non_leading_pass",
    "single_question_pass",
    "fact_whitelist_pass",
    "reflection_basis_pass",
)
MAX_RATINGS_BYTES = 16 * 1024 * 1024
MAX_CANDIDATE_PACKET_BYTES = 16 * 1024 * 1024


class AgreementInputError(ValueError):
    """Raised when blind-review ratings cannot be compared safely."""


def _load_jsonl(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgreementInputError(
            f"cannot read {label} {path}: {exc}"
        ) from exc
    if len(raw) > max_bytes:
        raise AgreementInputError(
            f"{label} exceeds the {max_bytes}-byte safety limit"
        )
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AgreementInputError(f"{label} must be UTF-8") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise AgreementInputError(
                f"{label} line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise AgreementInputError(
                f"{label} line {line_number}: must be a JSON object"
            )
        records.append(value)
    if not records:
        raise AgreementInputError(f"{label}: no records")
    return records, hashlib.sha256(raw).hexdigest()


def _required_text(record: dict[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgreementInputError(
            f"{label}: {field} must be a non-empty string"
        )
    return value.strip()


def _required_bool(record: dict[str, Any], field: str, label: str) -> bool:
    value = record.get(field)
    if type(value) is not bool:
        raise AgreementInputError(f"{label}: {field} must be a boolean")
    return value


def _required_score(record: dict[str, Any], field: str, label: str) -> int:
    value = record.get(field)
    if type(value) is not int or not 1 <= value <= 5:
        raise AgreementInputError(
            f"{label}: {field} must be an integer from 1 to 5"
        )
    return value


def _required_list(
    record: dict[str, Any],
    field: str,
    label: str,
) -> list[Any]:
    value = record.get(field)
    if not isinstance(value, list):
        raise AgreementInputError(f"{label}: {field} must be a list")
    return value


def _required_candidate_text(
    record: dict[str, Any],
    field: str,
    label: str,
) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgreementInputError(
            f"{label}: {field} must be a non-empty string"
        )
    return value


def _validate_candidate_packet(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], set[str]]:
    candidates_by_context: dict[str, dict[str, str]] = {}
    exact_tie_context_ids: set[str] = set()
    for line_number, record in enumerate(records, start=1):
        label = f"candidate packet line {line_number}"
        context_id = _required_text(record, "context_id", label)
        if context_id in candidates_by_context:
            raise AgreementInputError(
                f"{label}: duplicate context_id {context_id}"
            )
        raw_candidates = _required_list(record, "candidates", label)
        if len(raw_candidates) != 3:
            raise AgreementInputError(
                f"{label}: exactly 3 candidates are required"
            )
        candidate_map: dict[str, str] = {}
        for candidate_index, candidate in enumerate(
            raw_candidates,
            start=1,
        ):
            candidate_label = f"{label} candidate {candidate_index}"
            if not isinstance(candidate, dict):
                raise AgreementInputError(
                    f"{candidate_label}: must be an object"
                )
            candidate_id = _required_text(
                candidate,
                "candidate_id",
                candidate_label,
            )
            if candidate_id in candidate_map:
                raise AgreementInputError(
                    f"{candidate_label}: duplicate candidate_id "
                    f"{candidate_id}"
                )
            candidate_map[candidate_id] = _required_candidate_text(
                candidate,
                "candidate_text",
                candidate_label,
            )

        duplicate_sizes = sorted(Counter(candidate_map.values()).values())
        if duplicate_sizes == [1, 1, 1]:
            pass
        elif duplicate_sizes == [1, 2]:
            exact_tie_context_ids.add(context_id)
        else:
            raise AgreementInputError(
                f"{label}: exact-text collisions must contain at most one "
                "duplicate candidate pair"
            )
        candidates_by_context[context_id] = candidate_map
    return candidates_by_context, exact_tie_context_ids


def _validate_ratings(
    records: list[dict[str, Any]],
    candidates_by_context: dict[str, dict[str, str]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
]:
    candidate_ratings: list[dict[str, Any]] = []
    preferences: list[dict[str, Any]] = []
    reviewer_contexts: set[tuple[str, str]] = set()
    reviewers_by_context: dict[str, set[str]] = defaultdict(set)
    candidate_ids_by_context: dict[str, set[str]] = {}

    for line_number, record in enumerate(records, start=1):
        label = f"ratings line {line_number}"
        context_id = _required_text(record, "context_id", label)
        reviewer_id = _required_text(record, "reviewer_id", label)
        packet_candidates = candidates_by_context.get(context_id)
        if packet_candidates is None:
            raise AgreementInputError(
                f"{label}: context_id {context_id} is not in candidate packet"
            )
        reviewer_context = (context_id, reviewer_id)
        if reviewer_context in reviewer_contexts:
            raise AgreementInputError(
                f"{label}: duplicate review by {reviewer_id} for {context_id}"
            )
        reviewer_contexts.add(reviewer_context)
        reviewers_by_context[context_id].add(reviewer_id)

        raw_ratings = _required_list(record, "candidate_ratings", label)
        if len(raw_ratings) != 3:
            raise AgreementInputError(
                f"{label}: exactly 3 candidate ratings are required"
            )
        rated_ids: set[str] = set()
        validated_for_review: list[dict[str, Any]] = []
        for rating_index, rating in enumerate(raw_ratings, start=1):
            rating_label = f"{label} candidate rating {rating_index}"
            if not isinstance(rating, dict):
                raise AgreementInputError(
                    f"{rating_label}: must be an object"
                )
            candidate_id = _required_text(
                rating,
                "candidate_id",
                rating_label,
            )
            if candidate_id in rated_ids:
                raise AgreementInputError(
                    f"{rating_label}: duplicate candidate_id {candidate_id}"
                )
            rated_ids.add(candidate_id)
            validated: dict[str, Any] = {
                "context_id": context_id,
                "reviewer_id": reviewer_id,
                "candidate_id": candidate_id,
            }
            for field in SCORE_FIELDS:
                validated[field] = _required_score(
                    rating,
                    field,
                    rating_label,
                )
            for field in PASS_FIELDS:
                validated[field] = _required_bool(
                    rating,
                    field,
                    rating_label,
                )
            validated_for_review.append(validated)

        expected_ids = candidate_ids_by_context.setdefault(
            context_id,
            set(rated_ids),
        )
        if rated_ids != set(packet_candidates):
            raise AgreementInputError(
                f"{label}: candidate IDs must exactly match candidate packet "
                f"for {context_id}"
            )
        if rated_ids != expected_ids:
            raise AgreementInputError(
                f"{label}: candidate IDs differ from other reviewers for "
                f"{context_id}"
            )

        preferred_candidate_id = _required_text(
            record,
            "baseline_humanistic_preference",
            label,
        )
        if preferred_candidate_id not in rated_ids:
            raise AgreementInputError(
                f"{label}: baseline_humanistic_preference must select a "
                "rated candidate"
            )
        candidate_ratings.extend(validated_for_review)
        preferences.append(
            {
                "context_id": context_id,
                "reviewer_id": reviewer_id,
                "preferred_candidate_id": preferred_candidate_id,
            }
        )

    insufficient = {
        context_id: len(reviewers)
        for context_id, reviewers in reviewers_by_context.items()
        if len(reviewers) < 2
    }
    if insufficient:
        raise AgreementInputError(
            "ratings: each context requires at least 2 independent reviewers; "
            f"insufficient={dict(sorted(insufficient.items()))}"
        )
    missing_contexts = sorted(
        set(candidates_by_context) - set(reviewers_by_context)
    )
    if missing_contexts:
        raise AgreementInputError(
            "ratings and candidate packet must cover the same contexts; "
            f"missing_ratings={missing_contexts}"
        )

    candidate_slots_by_context = {
        context_id: {
            candidate_id: slot
            for slot, candidate_id in enumerate(
                candidates_by_context[context_id],
                start=1,
            )
        }
        for context_id in candidate_ids_by_context
    }
    for preference in preferences:
        preference["preference_slot"] = candidate_slots_by_context[
            preference["context_id"]
        ][preference["preferred_candidate_id"]]

    coverage = {
        "context_count": len(reviewers_by_context),
        "reviewer_count": len(
            {
                reviewer_id
                for reviewers in reviewers_by_context.values()
                for reviewer_id in reviewers
            }
        ),
        "independent_review_count": len(records),
        "candidate_rating_count": len(candidate_ratings),
    }
    return candidate_ratings, preferences, coverage


def _candidate_pairs(
    ratings: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    ratings_by_item: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)
    for rating in ratings:
        ratings_by_item[
            (rating["context_id"], rating["candidate_id"])
        ].append(rating)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item_ratings in ratings_by_item.values():
        ordered = sorted(
            item_ratings,
            key=lambda rating: rating["reviewer_id"],
        )
        pairs.extend(combinations(ordered, 2))
    return pairs


def _preference_pairs(
    preferences: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    preferences_by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for preference in preferences:
        preferences_by_context[preference["context_id"]].append(preference)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for context_preferences in preferences_by_context.values():
        ordered = sorted(
            context_preferences,
            key=lambda preference: preference["reviewer_id"],
        )
        pairs.extend(combinations(ordered, 2))
    return pairs


def _pair_coverage(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, int]:
    return {
        "reviewer_pair_count": len(
            {
                (left["reviewer_id"], right["reviewer_id"])
                for left, right in pairs
            }
        ),
        "reviewer_pair_context_count": len(
            {
                (
                    left["context_id"],
                    left["reviewer_id"],
                    right["reviewer_id"],
                )
                for left, right in pairs
            }
        ),
        "pairwise_comparison_count": len(pairs),
    }


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        raise AgreementInputError("cannot compute a mean without comparisons")
    return statistics.fmean(materialized)


def _rounded(value: float) -> float:
    return round(value, 4)


def _cohen_kappa(
    values: list[tuple[Any, Any]],
) -> dict[str, float | str | None]:
    if not values:
        raise AgreementInputError(
            "cannot compute Cohen's kappa without comparisons"
        )
    comparison_count = len(values)
    agreement_count = sum(left == right for left, right in values)
    observed_agreement = agreement_count / comparison_count
    left_counts = Counter(left for left, _ in values)
    right_counts = Counter(right for _, right in values)
    categories = set(left_counts) | set(right_counts)
    expected_agreement = sum(
        (left_counts[category] / comparison_count)
        * (right_counts[category] / comparison_count)
        for category in categories
    )
    denominator = 1.0 - expected_agreement
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        coefficient: float | None = (
            1.0 if math.isclose(observed_agreement, 1.0) else None
        )
        status = (
            "perfect_agreement_without_variation"
            if coefficient == 1.0
            else "undefined_zero_expected_disagreement"
        )
    else:
        coefficient = (observed_agreement - expected_agreement) / denominator
        status = "defined"
    return {
        "cohen_kappa": (
            _rounded(coefficient) if coefficient is not None else None
        ),
        "cohen_kappa_status": status,
        "observed_agreement_rate": _rounded(observed_agreement),
        "expected_agreement_rate": _rounded(expected_agreement),
    }


def _quadratic_weighted_kappa(
    values: list[tuple[int, int]],
) -> dict[str, float | str | None]:
    if not values:
        raise AgreementInputError(
            "cannot compute quadratic weighted kappa without comparisons"
        )
    categories = tuple(range(1, 6))
    comparison_count = len(values)
    scale_span_squared = float((categories[-1] - categories[0]) ** 2)

    def disagreement_weight(left: int, right: int) -> float:
        return ((left - right) ** 2) / scale_span_squared

    observed_disagreement = _mean(
        disagreement_weight(left, right) for left, right in values
    )
    left_counts = Counter(left for left, _ in values)
    right_counts = Counter(right for _, right in values)
    expected_disagreement = sum(
        disagreement_weight(left, right)
        * (left_counts[left] / comparison_count)
        * (right_counts[right] / comparison_count)
        for left in categories
        for right in categories
    )
    if math.isclose(expected_disagreement, 0.0, abs_tol=1e-12):
        coefficient: float | None = (
            1.0 if math.isclose(observed_disagreement, 0.0) else None
        )
        status = (
            "perfect_agreement_without_variation"
            if coefficient == 1.0
            else "undefined_zero_expected_disagreement"
        )
    else:
        coefficient = 1.0 - observed_disagreement / expected_disagreement
        status = "defined"
    return {
        "quadratic_weighted_kappa": (
            _rounded(coefficient) if coefficient is not None else None
        ),
        "quadratic_weighted_kappa_status": status,
        "observed_weighted_disagreement": _rounded(
            observed_disagreement
        ),
        "expected_weighted_disagreement": _rounded(
            expected_disagreement
        ),
    }


def analyze_inter_rater_agreement(
    *,
    ratings_path: Path,
    candidate_packet_path: Path,
) -> dict[str, Any]:
    """Return descriptive pairwise agreement metrics for blind ratings."""

    candidate_records, candidate_packet_sha256 = _load_jsonl(
        candidate_packet_path,
        label="candidate packet",
        max_bytes=MAX_CANDIDATE_PACKET_BYTES,
    )
    candidates_by_context, exact_tie_context_ids = (
        _validate_candidate_packet(candidate_records)
    )
    rating_records, ratings_sha256 = _load_jsonl(
        ratings_path,
        label="ratings",
        max_bytes=MAX_RATINGS_BYTES,
    )
    ratings, preferences, coverage = _validate_ratings(
        rating_records,
        candidates_by_context,
    )
    candidate_pairs = _candidate_pairs(ratings)
    all_preference_pairs = _preference_pairs(preferences)
    if not candidate_pairs or not all_preference_pairs:
        raise AgreementInputError(
            "ratings: no reviewer pairs available for analysis"
        )
    excluded_preference_pairs = [
        pair
        for pair in all_preference_pairs
        if pair[0]["context_id"] in exact_tie_context_ids
    ]
    preference_pairs = [
        pair
        for pair in all_preference_pairs
        if pair[0]["context_id"] not in exact_tie_context_ids
    ]

    candidate_coverage = _pair_coverage(candidate_pairs)
    scale_metrics: dict[str, dict[str, Any]] = {}
    for field in SCORE_FIELDS:
        values = [
            (int(left[field]), int(right[field]))
            for left, right in candidate_pairs
        ]
        exact_agreement_count = sum(left == right for left, right in values)
        scale_metrics[field] = {
            **candidate_coverage,
            "exact_agreement_count": exact_agreement_count,
            "exact_agreement_rate": _rounded(
                exact_agreement_count / len(values)
            ),
            "mean_absolute_difference": _rounded(
                _mean(abs(left - right) for left, right in values)
            ),
            **_quadratic_weighted_kappa(values),
        }

    boolean_metrics: dict[str, dict[str, Any]] = {}
    for field in PASS_FIELDS:
        values = [
            (bool(left[field]), bool(right[field]))
            for left, right in candidate_pairs
        ]
        agreement_count = sum(left == right for left, right in values)
        boolean_metrics[field] = {
            **candidate_coverage,
            "agreement_count": agreement_count,
            "agreement_rate": _rounded(agreement_count / len(values)),
            **_cohen_kappa(values),
        }

    preference_exclusion_metrics = {
        "excluded_exact_tie_context_count": len(exact_tie_context_ids),
        "excluded_exact_tie_comparison_count": len(
            excluded_preference_pairs
        ),
        "excluded_exact_tie_reason": (
            "an exact-text duplicate pair makes opaque candidate-ID choice "
            "non-identifiable as a substantive preference"
        ),
    }
    if preference_pairs:
        preference_values = [
            (
                int(left["preference_slot"]),
                int(right["preference_slot"]),
            )
            for left, right in preference_pairs
        ]
        preference_agreement_count = sum(
            left == right for left, right in preference_values
        )
        preference_metrics: dict[str, Any] = {
            **_pair_coverage(preference_pairs),
            **preference_exclusion_metrics,
            "agreement_count": preference_agreement_count,
            "agreement_rate": _rounded(
                preference_agreement_count / len(preference_values)
            ),
            **_cohen_kappa(preference_values),
        }
    else:
        preference_metrics = {
            **_pair_coverage(preference_pairs),
            **preference_exclusion_metrics,
            "agreement_count": 0,
            "agreement_rate": None,
            "cohen_kappa": None,
            "cohen_kappa_status": (
                "undefined_no_eligible_non_tie_comparisons"
            ),
            "observed_agreement_rate": None,
            "expected_agreement_rate": None,
        }
    return {
        "schema_version": "1.0",
        "analyzer": ANALYZER_ID,
        "status": "ANALYZED",
        "release_gate": False,
        "input": {
            "ratings": str(ratings_path),
            "ratings_sha256": ratings_sha256,
            "candidate_packet": str(candidate_packet_path),
            "candidate_packet_sha256": candidate_packet_sha256,
        },
        "method": {
            "pairing": (
                "all unordered reviewer pairs sharing the same blind item"
            ),
            "candidate_item_key": ["context_id", "candidate_id"],
            "preference_item_key": ["context_id"],
            "preference_category_normalization": (
                "opaque candidate IDs are mapped to their deterministic "
                "within-context candidate-packet slots; no arm key is used"
            ),
            "preference_exact_tie_handling": (
                "exact-text duplicate contexts are excluded from preference "
                "agreement only; scale and boolean agreement still include "
                "their candidate ratings"
            ),
            "interpretation": (
                "descriptive only; no agreement threshold is a release gate"
            ),
        },
        "coverage": coverage,
        "scale_fields": scale_metrics,
        "boolean_fields": boolean_metrics,
        "preference": preference_metrics,
    }


def _write_new_private_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report post-generation inter-rater agreement for Humanistic "
            "Interviewer v1 blind ratings and candidate packet without an "
            "arm key."
        )
    )
    parser.add_argument("--ratings", required=True)
    parser.add_argument("--candidate-packet", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    ratings_path = Path(args.ratings).expanduser().resolve()
    candidate_packet_path = Path(
        args.candidate_packet
    ).expanduser().resolve()
    try:
        report = analyze_inter_rater_agreement(
            ratings_path=ratings_path,
            candidate_packet_path=candidate_packet_path,
        )
    except AgreementInputError as exc:
        error_report = {
            "schema_version": "1.0",
            "analyzer": ANALYZER_ID,
            "status": "INVALID",
            "release_gate": False,
            "error": str(exc),
        }
        print(
            json.dumps(
                error_report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    rendered = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        try:
            _write_new_private_report(output_path, rendered + "\n")
        except FileExistsError:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "analyzer": ANALYZER_ID,
                        "status": "BLOCKED",
                        "release_gate": False,
                        "error": (
                            "output already exists; agreement reports are "
                            "never overwritten"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 2
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
