"""CLI contract tests use a fake Notion boundary, never live UI evidence."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teacher_planner.cli import main, verify_remote
from teacher_planner.install import install
from teacher_planner.layout_cli import load_evidence
from teacher_planner.layout_contract import (active_sections_for_config, load_contract,
                                            render_page, resolve_page_layout)
from teacher_planner.layout_verify import required_visual_checks
from teacher_planner.model import config, dashboard_views
from test_layout_verify import PAGE_IDS, SYNC_ID, fragments, png_fixture
from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


class LayoutCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state_path = self.base / "state.json"
        self.evidence_path = self.base / "layout-evidence.json"
        self.api = FakeNotion()
        self.config = config(ROOT / "config.example.json")
        install(self.api, self.config, self.api.parent, self.state_path)
        self.state = json.loads(self.state_path.read_text())
        png_fixture(self.base / "synthetic-test-only.png")
        self.snapshot = self.evidence()
        self.save()
        self.api.calls.clear()

    def evidence(self):
        pages = self.state["dashboard"]["pages"]
        stamp = datetime.now(timezone.utc).isoformat()
        result = {"schema_version": 1, "contract_version": load_contract()["version"],
                  "expected_page_ids": pages.copy(), "active_sections": {}, "pages": {},
                  "expected_data_source_ids": {key: value["data_source_id"] for key, value in self.state["databases"].items()},
                  "expected_synced_block_id": self.state["dashboard"]["matrix_sync_id"],
                  "core_verification": {"checked_at": stamp, "status": "pass", "page_ids": pages.copy()}}
        views = dashboard_views(self.config)
        groups = self.state["dashboard"]["view_groups"]
        for page_key, identifier in pages.items():
            selected = active_sections_for_config(self.config, page_key)
            result["active_sections"][page_key] = selected
            content, bindings = fragments(page_key, selected)
            for section, binding in bindings.items():
                group = next(group for group in groups if group["page"] == page_key and group["section"] == section)
                view = next(view for view in views if view["workspace_page"] == page_key and view["section"] == section)
                source_id = self.state["databases"][view["source"]]["data_source_id"]
                content[section] = (content[section].replace(binding["data_source_id"], source_id)
                                    .replace(binding["database_id"], group["container_id"]))
                bindings[section] = {"data_source_id": source_id, "database_id": group["container_id"]}
            markdown = render_page(page_key, content)
            for old, real in ((PAGE_IDS[key], value) for key, value in pages.items()):
                markdown = markdown.replace(old, real)
            markdown = markdown.replace(SYNC_ID, result["expected_synced_block_id"])
            result["pages"][page_key] = {"page_id": identifier, "fetched_at": stamp, "markdown": markdown,
                "properties": {"full_width": True, "small_text": False}, "source_bindings": bindings,
                "screenshots": [{"path": "synthetic-test-only.png", "page_id": identifier, "captured_at": stamp,
                    "viewport": {"width": 1440, "height": 900}, "coverage": "full_page", "observations": [
                        {"check": name, "result": "pass", "note": "Synthetic unit-test observation"}
                        for name in required_visual_checks(resolve_page_layout(page_key, selected))]}]}
        return result

    def save(self):
        self.evidence_path.write_text(json.dumps(self.snapshot))

    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with patch("teacher_planner.cli.Client", return_value=self.api), patch("sys.stdout", out), patch("sys.stderr", err):
            code = main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_plan_is_offline_and_never_claims_applied_layout(self):
        with patch("teacher_planner.cli.Client", side_effect=AssertionError("no network")), patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(0, main(["layout-plan", "--config", str(ROOT / "config.example.json")]))
        report = json.loads(out.getvalue())
        self.assertFalse(report["applied"])
        self.assertFalse(report["layout_complete"])
        self.assertTrue(report["page_settings"]["full_width"])

    def test_install_marks_layout_acceptance_pending_without_breaking_sync_checkpoint(self):
        self.assertTrue(self.state["complete"])
        self.assertEqual("pending", self.state["layout_acceptance"]["status"])
        self.assertFalse(self.state["layout_acceptance"]["layout_complete"])

    def test_core_only_is_explicit_and_final_without_evidence_returns_nonzero(self):
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--core-only")
        self.assertEqual((0, ""), (code, err))
        self.assertIn("설치 완료가 아닙니다", out)
        code, out, err = self.run_cli("verify", "--state", str(self.state_path))
        self.assertEqual((1, ""), (code, err))
        report = json.loads(out)
        self.assertTrue(report["core_complete"])
        self.assertFalse(report["installation_complete"])

    def test_complete_snapshot_and_remote_core_both_required(self):
        before = self.state_path.read_bytes()
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual((0, ""), (code, err))
        report = json.loads(out)
        self.assertTrue(report["installation_complete"])
        self.assertTrue(report["core_complete"])
        self.assertTrue(report["layout_complete"])
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_current_remote_core_result_overrides_absent_snapshot_attestation(self):
        self.snapshot.pop("core_verification")
        self.save()
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual((0, ""), (code, err))
        report = json.loads(out)
        self.assertTrue(report["complete"])
        self.assertEqual("pass", report["status"])
        self.assertFalse(any(check["category"] == "core" and check["status"] != "pass" for check in report["checks"]))

    def test_missing_ui_cannot_become_final_completion(self):
        self.snapshot["pages"]["home"]["screenshots"] = []
        self.save()
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual((1, ""), (code, err))
        report = json.loads(out)
        self.assertTrue(report["core_complete"])
        self.assertFalse(report["installation_complete"])

    def test_state_page_bindings_reject_another_notebook(self):
        self.snapshot["expected_page_ids"]["home"] = "00000000000000000000000000009999"
        self.save()
        with self.assertRaises(ValueError):
            load_evidence(self.evidence_path, state=self.state)

    def test_page_uuid_hyphen_format_does_not_change_identity(self):
        self.snapshot["expected_page_ids"] = {key: value.replace("-", "")
                                              for key, value in self.snapshot["expected_page_ids"].items()}
        self.save()
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual((0, ""), (code, err))
        self.assertTrue(json.loads(out)["installation_complete"])

    def test_selected_module_cannot_be_omitted_to_pass_layout(self):
        self.snapshot["active_sections"]["home"].remove("attendance")
        self.save()
        with self.assertRaisesRegex(ValueError, "선택한 모듈"):
            load_evidence(self.evidence_path, state=self.state)

    def test_unset_active_sections_does_not_bypass_state_module_check(self):
        self.snapshot.pop("active_sections")
        self.save()
        with self.assertRaises(ValueError):
            load_evidence(self.evidence_path, state=self.state)

    def test_fabricated_source_inventory_cannot_override_state_sources(self):
        page = self.snapshot["pages"]["home"]
        old = page["source_bindings"]["tasks"]["data_source_id"]
        fake = "00000000000000000000000000009999"
        page["markdown"] = page["markdown"].replace("collection://" + old, "collection://" + fake)
        for binding in page["source_bindings"].values():
            if binding["data_source_id"] == old:
                binding["data_source_id"] = fake
        self.snapshot["expected_data_source_ids"]["fabricated"] = fake
        self.save()
        code, out, _ = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual(1, code, out)

    def test_snapshot_cannot_skip_functional_source_and_view_checks(self):
        view = self.state["objects"]["home:weekly"]["id"]
        self.api.objects["/views/" + view]["data_source_id"] = "00000000000000000000000000009999"
        code, out, err = self.run_cli("verify", "--state", str(self.state_path), "--layout-snapshot", str(self.evidence_path))
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("원본 불일치", err)

    def test_only_snapshot_path_skips_legacy_flat_placement_check(self):
        for arguments, expected in ((["--core-only"], True), ([], True), (["--layout-snapshot", str(self.evidence_path)], False)):
            with patch("teacher_planner.cli.verify_remote", wraps=verify_remote) as remote:
                self.run_cli("verify", "--state", str(self.state_path), *arguments)
                self.assertEqual(expected, remote.call_args.kwargs["check_placement"])

    def test_core_only_and_final_snapshot_flags_are_mutually_exclusive(self):
        code, _, err = self.run_cli("verify", "--state", str(self.state_path), "--core-only", "--layout-snapshot", str(self.evidence_path))
        self.assertEqual(1, code)
        self.assertIn("함께 사용할 수 없습니다", err)


if __name__ == "__main__":
    unittest.main()
