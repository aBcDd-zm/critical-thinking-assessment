from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.agents.humanistic_evaluation_context import (
    HumanisticPilotContext,
    load_context_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_RELATIVE_PATH = Path(
    "backend/tests/fixtures/humanistic_interviewer/"
    "pilot_context_manifest_v1.json"
)
SOURCE_MANIFEST_PATH = REPO_ROOT / MANIFEST_RELATIVE_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HumanisticContextFreezeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir_context.name) / "repo"
        self.manifest = json.loads(
            SOURCE_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        relative_paths = {
            self.manifest["development_contexts"]["repo_relative_path"],
            self.manifest["locked_test_contexts"]["repo_relative_path"],
            self.manifest["review_examples"]["repo_relative_path"],
            *(
                item["repo_relative_path"]
                for item in self.manifest["freeze_artifacts"]
            ),
        }
        for relative_path in relative_paths:
            source = REPO_ROOT / relative_path
            destination = self.repo_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.manifest_path = self.repo_root / MANIFEST_RELATIVE_PATH
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest()

    def tearDown(self) -> None:
        self.temp_dir_context.cleanup()

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _asset_path(self, key: str) -> Path:
        return self.repo_root / self.manifest[key]["repo_relative_path"]

    def _update_asset_hash(self, relative_path: str) -> None:
        digest = _sha256(self.repo_root / relative_path)
        for key in (
            "development_contexts",
            "locked_test_contexts",
            "review_examples",
        ):
            if self.manifest[key]["repo_relative_path"] == relative_path:
                self.manifest[key]["sha256"] = digest
                amendment = self.manifest.get("generation_contract_amendment")
                if amendment is not None:
                    amendment[f"{key}_sha256"] = digest
                self._write_manifest()
                return
        for artifact in self.manifest["freeze_artifacts"]:
            if artifact["repo_relative_path"] == relative_path:
                artifact["sha256"] = digest
                self._write_manifest()
                return
        raise AssertionError(f"unknown manifest asset: {relative_path}")

    def _rewrite_jsonl(self, key: str, transform) -> None:
        path = self._asset_path(key)
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        transform(rows)
        path.write_text(
            "".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        self._update_asset_hash(self.manifest[key]["repo_relative_path"])

    def _freeze_bundle(self) -> None:
        for key in ("development_contexts", "locked_test_contexts"):
            self._rewrite_jsonl(
                key,
                lambda rows: [row.update(status="frozen_v1") for row in rows],
            )
        self.manifest["status"] = "frozen_v1"
        self.manifest["freeze_record"] = {
            "frozen_at": "2026-07-28",
            "approved_by_role": "member_a_psy",
            "approved_gate_ids": [
                f"FREEZE-{letter}" for letter in "ABCDEFGH"
            ],
            "approved_rejection_test_ids": [
                f"RJ-{index:02d}" for index in range(1, 13)
            ],
            "candidate_generation_started": False,
        }
        self._write_manifest()

    def test_rj01_frozen_mode_rejects_provisional_manifest(self) -> None:
        for key in ("development_contexts", "locked_test_contexts"):
            self._rewrite_jsonl(
                key,
                lambda rows: [
                    row.update(status="provisional_synthetic") for row in rows
                ],
            )
        self.manifest["status"] = "provisional_synthetic"
        self.manifest["freeze_record"] = None
        self.manifest["generation_contract_amendment"] = None
        self.manifest["generation_reliability_amendment"] = None
        self.manifest["generation_reliability_amendment_v2"] = None
        self.manifest["generation_reliability_amendment_v3"] = None
        self.manifest["generation_reliability_amendment_v4"] = None
        self.manifest["generation_reliability_amendment_v5"] = None
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "requires manifest status frozen_v1"):
            load_context_manifest(
                self.manifest_path,
                repo_root=self.repo_root,
                require_frozen=True,
            )

    def test_complete_frozen_bundle_can_pass_the_context_gate(self) -> None:
        self._freeze_bundle()

        records = load_context_manifest(
            self.manifest_path,
            repo_root=self.repo_root,
            require_frozen=True,
        )

        self.assertEqual(len(records), 48)
        self.assertEqual({item.status for item in records}, {"frozen_v1"})

    def test_rj02_mixed_context_status_is_rejected(self) -> None:
        self._freeze_bundle()
        self._rewrite_jsonl(
            "development_contexts",
            lambda rows: rows[0].update(status="provisional_synthetic"),
        )

        with self.assertRaisesRegex(ValueError, "must share one status"):
            load_context_manifest(
                self.manifest_path,
                repo_root=self.repo_root,
                require_frozen=True,
            )

    def test_rj03_hash_mismatch_is_rejected(self) -> None:
        path = self._asset_path("development_contexts")
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj03_missing_asset_is_rejected(self) -> None:
        self._asset_path("locked_test_contexts").unlink()

        with self.assertRaisesRegex(ValueError, "does not exist"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj04_path_traversal_is_rejected(self) -> None:
        self.manifest["development_contexts"]["repo_relative_path"] = (
            "../pilot_contexts_development_v1.jsonl"
        )
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "repository-relative"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj04_symlink_asset_is_rejected(self) -> None:
        path = self._asset_path("development_contexts")
        alternate = path.with_name("development_copy.jsonl")
        shutil.copy2(path, alternate)
        path.unlink()
        path.symlink_to(alternate)

        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj05_missing_required_freeze_artifact_is_rejected(self) -> None:
        self.manifest["freeze_artifacts"].pop()
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "exact required freeze artifacts"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj05_duplicate_freeze_artifact_path_is_rejected(self) -> None:
        self.manifest["freeze_artifacts"][-1]["repo_relative_path"] = self.manifest[
            "freeze_artifacts"
        ][-2]["repo_relative_path"]
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "exact required freeze artifacts"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj07_split_distribution_drift_is_rejected(self) -> None:
        self._rewrite_jsonl(
            "development_contexts",
            lambda rows: rows[0].update(split="dev"),
        )

        with self.assertRaisesRegex(ValueError, "split must remain 32/8/8"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj07_dimension_distribution_drift_is_rejected(self) -> None:
        self._rewrite_jsonl(
            "development_contexts",
            lambda rows: rows[0]["frozen_plan"].update(
                target_dimension="evidence_evaluation"
            ),
        )

        with self.assertRaisesRegex(ValueError, "dimension distribution changed"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj08_duplicate_context_id_is_rejected(self) -> None:
        review_context_ids = {
            json.loads(line)["context_id"]
            for line in self._asset_path("review_examples")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        }

        def duplicate_unreferenced_id(rows: list[dict]) -> None:
            unreferenced = [
                row for row in rows if row["context_id"] not in review_context_ids
            ]
            self.assertGreaterEqual(len(unreferenced), 2)
            unreferenced[1]["context_id"] = unreferenced[0]["context_id"]

        self._rewrite_jsonl(
            "development_contexts",
            duplicate_unreferenced_id,
        )

        with self.assertRaisesRegex(ValueError, "context_id must be unique"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj09_locked_set_mismatch_is_rejected(self) -> None:
        self.manifest["new_locked_context_ids"][-1] = "HIV1-I10"
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "approved v1 set"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj09_retired_locked_id_reentry_is_rejected(self) -> None:
        self._rewrite_jsonl(
            "development_contexts",
            lambda rows: rows[0].update(context_id="HIV1-O04"),
        )

        with self.assertRaisesRegex(ValueError, "retired locked context re-entered"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj10_review_reference_to_locked_context_is_rejected(self) -> None:
        self._rewrite_jsonl(
            "review_examples",
            lambda rows: rows[0].update(context_id="HIV1-O05"),
        )

        with self.assertRaisesRegex(ValueError, "reference locked context"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj10_review_reference_to_unknown_context_is_rejected(self) -> None:
        self._rewrite_jsonl(
            "review_examples",
            lambda rows: rows[0].update(context_id="HIV1-Z99"),
        )

        with self.assertRaisesRegex(ValueError, "reference unknown context"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj10_locked_content_copied_into_review_examples_is_rejected(
        self,
    ) -> None:
        locked_first = json.loads(
            self._asset_path("locked_test_contexts")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        locked_user_text = locked_first["visible_history"][-1]["content"]
        self._rewrite_jsonl(
            "review_examples",
            lambda rows: rows[0].update(candidate_text=locked_user_text),
        )

        with self.assertRaisesRegex(ValueError, "locked content leaked"):
            load_context_manifest(self.manifest_path, repo_root=self.repo_root)

    def test_rj11_runtime_semantic_mutations_are_rejected(self) -> None:
        locked_rows = [
            json.loads(line)
            for line in self._asset_path("locked_test_contexts")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        mutations = []

        stage_mismatch = copy.deepcopy(locked_rows[0])
        stage_mismatch["visible_history"][-1]["stage_code"] = (
            "s2_evidence_verification"
        )
        mutations.append(stage_mismatch)

        action_mismatch = copy.deepcopy(locked_rows[0])
        action_mismatch["frozen_plan"]["action"] = "CLARIFY"
        mutations.append(action_mismatch)

        reflection_mismatch = copy.deepcopy(locked_rows[0])
        reflection_mismatch["frozen_plan"]["reflection_basis_turn_ids"] = [999]
        reflection_mismatch["reflection_review"]["turn_ids"] = [999]
        mutations.append(reflection_mismatch)

        event_mismatch = copy.deepcopy(locked_rows[3])
        event_mismatch["frozen_plan"]["release_unit_code"] = "wrong_unit"
        mutations.append(event_mismatch)

        repair_scoring = copy.deepcopy(locked_rows[6])
        repair_scoring["formal_answer"] = True
        mutations.append(repair_scoring)

        for record in mutations:
            with self.subTest(context_id=record["context_id"]):
                with self.assertRaises(ValueError):
                    HumanisticPilotContext.model_validate(record)

    def test_rj12_documented_module_cli_returns_blocked_without_generation(
        self,
    ) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.evaluate_humanistic_interviewer_v1"],
            cwd=REPO_ROOT / "backend",
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["gates"], [])


if __name__ == "__main__":
    unittest.main()
