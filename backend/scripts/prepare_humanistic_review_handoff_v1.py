#!/usr/bin/env python3
"""Create redacted generation evidence or arm-blind rating templates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.humanistic_review_handoff import (
    HandoffValidationError,
    build_blank_rating_templates,
    build_evaluator_input_bundle,
    build_generation_receipt,
    build_unblinded_evaluator_ratings,
    sha256_file,
    write_blank_rating_templates,
    write_evaluator_input_bundle,
    write_generation_receipt,
    write_unblinded_evaluator_ratings,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the Humanistic Interviewer private-to-public review "
            "handoff without publishing candidate text, arm/case keys, or "
            "provenance records."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    receipt_parser = subparsers.add_parser(
        "generation-receipt",
        help=(
            "Validate a private complete generation manifest and write a "
            "redacted receipt"
        ),
    )
    receipt_parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help=(
            "Repository-external candidate_generation_manifest_v1.json"
        ),
    )
    receipt_parser.add_argument(
        "--exact-ties",
        type=Path,
        help=(
            "Repository-external sealed/exact_model_ties_v1.jsonl; required "
            "when the manifest declares one or more exact ties"
        ),
    )
    receipt_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New redacted receipt path; existing files are never overwritten",
    )
    ratings_parser = subparsers.add_parser(
        "ratings-template",
        help=(
            "Validate a private blind packet and write one reviewer's blank, "
            "arm-blind JSONL rating template"
        ),
    )
    ratings_parser.add_argument(
        "--packet",
        required=True,
        type=Path,
        help="Repository-external reviewer/blind_review_packet_v1.jsonl",
    )
    ratings_parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help=(
            "Validated generation_receipt_v1.json whose blind-packet hash "
            "must match"
        ),
    )
    ratings_parser.add_argument(
        "--reviewer-id",
        required=True,
        help="Pseudonymous reviewer identifier; do not use a real name",
    )
    ratings_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help=(
            "New repository-external private rating-template path; existing "
            "files are never overwritten"
        ),
    )

    unblind_parser = subparsers.add_parser(
        "unblind-ratings",
        help=(
            "Custodian-only conversion of frozen arm-blind reviews to the "
            "existing evaluator ratings contract"
        ),
    )
    unblind_parser.add_argument(
        "--ratings",
        required=True,
        action="append",
        type=Path,
        help=(
            "Repository-external completed reviewer ratings; repeat once per "
            "independently frozen reviewer file"
        ),
    )
    unblind_parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help=(
            "Validated generation_receipt_v1.json whose sealed-file hashes "
            "must match"
        ),
    )
    unblind_parser.add_argument(
        "--case-key",
        required=True,
        type=Path,
        help="Repository-external sealed/case_key_v1.jsonl",
    )
    unblind_parser.add_argument(
        "--arm-key",
        required=True,
        type=Path,
        help="Repository-external sealed/arm_key_v1.jsonl",
    )
    unblind_parser.add_argument(
        "--exact-ties",
        type=Path,
        help=(
            "Repository-external sealed/exact_model_ties_v1.jsonl; required "
            "when the receipt declares one or more exact ties"
        ),
    )
    unblind_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help=(
            "New repository-external evaluator ratings JSONL; existing files "
            "are never overwritten"
        ),
    )

    evaluator_parser = subparsers.add_parser(
        "prepare-evaluator-inputs",
        help=(
            "Custodian-only preparation of the complete context-keyed frozen "
            "evaluator input bundle"
        ),
    )
    evaluator_parser.add_argument(
        "--packet",
        required=True,
        type=Path,
        help="Repository-external reviewer/blind_review_packet_v1.jsonl",
    )
    evaluator_parser.add_argument(
        "--ratings",
        required=True,
        action="append",
        type=Path,
        help=(
            "Repository-external completed reviewer ratings; repeat once per "
            "independently frozen reviewer file"
        ),
    )
    evaluator_parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help="Validated generation_receipt_v1.json",
    )
    evaluator_parser.add_argument(
        "--case-key",
        required=True,
        type=Path,
        help="Repository-external sealed/case_key_v1.jsonl",
    )
    evaluator_parser.add_argument(
        "--arm-key",
        required=True,
        type=Path,
        help="Repository-external sealed/arm_key_v1.jsonl",
    )
    evaluator_parser.add_argument(
        "--exact-ties",
        type=Path,
        help=(
            "Repository-external sealed/exact_model_ties_v1.jsonl; required "
            "when the receipt declares one or more exact ties"
        ),
    )
    evaluator_parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help=(
            "New repository-external private bundle directory; existing paths "
            "are never overwritten"
        ),
    )
    return parser


def _result(status: str, **values: object) -> str:
    return json.dumps(
        {
            "schema_version": "humanistic_review_handoff_cli_result_v1",
            "status": status,
            **values,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generation-receipt":
            receipt = build_generation_receipt(
                manifest_path=args.manifest,
                exact_ties_path=args.exact_ties,
                repo_root=REPO_ROOT,
                expected_context_count=48,
            )
            write_generation_receipt(receipt, args.output)
            print(
                _result(
                    "COMPLETE",
                    command=args.command,
                    output=str(args.output.expanduser().resolve(strict=False)),
                    run_id=receipt.run_id,
                    context_count=receipt.counts.context_count,
                    candidate_count=receipt.counts.candidate_count,
                    exact_model_tie_count=(
                        receipt.counts.exact_model_tie_count
                    ),
                )
            )
            return 0

        if args.command == "ratings-template":
            templates = build_blank_rating_templates(
                packet_path=args.packet,
                receipt_path=args.receipt,
                reviewer_id=args.reviewer_id,
                repo_root=REPO_ROOT,
            )
            write_blank_rating_templates(
                templates,
                args.output,
                repo_root=REPO_ROOT,
            )
            print(
                _result(
                    "COMPLETE",
                    command=args.command,
                    output=str(args.output.expanduser().resolve(strict=False)),
                    reviewer_id=args.reviewer_id,
                    case_count=len(templates),
                    receipt_sha256=sha256_file(args.receipt),
                    packet_sha256=sha256_file(args.packet),
                )
            )
            return 0

        if args.command == "prepare-evaluator-inputs":
            bundle = build_evaluator_input_bundle(
                packet_path=args.packet,
                ratings_paths=args.ratings,
                receipt_path=args.receipt,
                case_key_path=args.case_key,
                arm_key_path=args.arm_key,
                exact_ties_path=args.exact_ties,
                repo_root=REPO_ROOT,
            )
            manifest = write_evaluator_input_bundle(
                bundle,
                args.output_dir,
                repo_root=REPO_ROOT,
            )
            resolved_output_dir = args.output_dir.expanduser().resolve(
                strict=False
            )
            print(
                _result(
                    "COMPLETE",
                    command=args.command,
                    output_dir=str(resolved_output_dir),
                    context_count=bundle.context_count,
                    independent_review_count=(
                        bundle.independent_review_count
                    ),
                    output_sha256=manifest["output_sha256"],
                    manifest_sha256=sha256_file(
                        resolved_output_dir
                        / "evaluator_input_manifest_v1.json"
                    ),
                )
            )
            return 0

        evaluator_ratings = build_unblinded_evaluator_ratings(
            ratings_paths=args.ratings,
            receipt_path=args.receipt,
            case_key_path=args.case_key,
            arm_key_path=args.arm_key,
            exact_ties_path=args.exact_ties,
            repo_root=REPO_ROOT,
        )
        input_hashes = {
            "receipt_sha256": sha256_file(args.receipt),
            "ratings_sha256": [
                sha256_file(path) for path in args.ratings
            ],
            "case_key_sha256": sha256_file(args.case_key),
            "arm_key_sha256": sha256_file(args.arm_key),
            "exact_ties_sha256": (
                sha256_file(args.exact_ties)
                if args.exact_ties is not None
                else None
            ),
        }
        write_unblinded_evaluator_ratings(
            evaluator_ratings,
            args.output,
            repo_root=REPO_ROOT,
        )
        print(
            _result(
                "COMPLETE",
                command=args.command,
                output=str(args.output.expanduser().resolve(strict=False)),
                output_sha256=sha256_file(args.output),
                context_count=len(
                    {
                        record["context_id"]
                        for record in evaluator_ratings
                    }
                ),
                independent_review_count=len(evaluator_ratings),
                input_hashes=input_hashes,
            )
        )
        return 0
    except (FileExistsError, HandoffValidationError, OSError) as exc:
        print(
            _result(
                "BLOCKED",
                command=args.command,
                error=f"{type(exc).__name__}: {exc}",
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
