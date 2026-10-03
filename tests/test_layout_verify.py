"""Offline evidence fixtures: these tests do not claim a Notion UI was checked."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from teacher_planner.layout_contract import load_contract, render_page, resolve_page_layout
from teacher_planner.layout_verify import required_visual_checks, verify_layout_snapshot


NOW = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
PAGE_IDS = {key: f"{index:032x}" for index, key in enumerate(load_contract()["pages"], 1)}
SYNC_ID = "00000000000000000000000000000099"


def png_fixture(path):
    """Small valid synthetic image for file checks, never used as UI evidence."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    width, height = 1440, 900
    pixels = (b"\0" + b"\xff\xff\xff" * width) * height
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def fragments(page_key, selected=None):
    page = resolve_page_layout(page_key, selected)
    result, bindings = {}, {}
    for index, (key, spec) in enumerate(page["sections"].items(), 100):
        title = "## " + spec["title"] + "\n"
        if key == "nav":
            result[key] = ' · '.join(f'<mention-page url="https://notion.so/{identity}">이동</mention-page>'
                                     for identity in PAGE_IDS.values())
        elif key == "intro":
            result[key] = "**2026학년도** · 가상 교사 · 과학"
        elif "cards" in spec:
            result[key] = ["빈 기록 양식 " + str(number) for number in range(spec["cards"]["max"])]
        elif key == "matrix":
            table = ('<table fit-page-width="true" header-row="true"><tr>'
                     + ''.join('<td>' + name + '</td>' for name in load_contract()["timetable"]["columns"])
                     + '</tr><tr><td>1교시</td><td>미설정</td><td></td><td></td><td></td><td></td><td></td></tr></table>')
            tag = "synced_block_reference" if page_key == "home" else "synced_block"
            result[key] = title + f'<{tag} url="https://notion.so/{PAGE_IDS["teaching"]}#{SYNC_ID}">\n{table}\n</{tag}>'
        elif spec.get("database_blocks"):
            source, database = f"{index:032x}", f"{index + 1000:032x}"
            body = f'<database url="https://notion.so/{database}" data-source-url="collection://{source}" inline="true"></database>'
            result[key] = body if spec.get("toggle") else title + body
            bindings[key] = {"data_source_id": source, "database_id": database}
        elif spec.get("toggle"):
            result[key] = "보관된 원본 바로가기"
        elif key == "briefing":
            result[key] = '<callout icon="💡" color="gray_bg">\n**' + spec["title"] + '**\n가상 안내\n</callout>'
        elif key == "meals":
            result[key] = '<callout icon="🍱" color="yellow_bg">\n**오늘의 중식 · 2026-10-03**\n연결 전\n</callout>'
        else:
            result[key] = title + "가상 안내"
    return result, bindings


class LayoutVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        png_fixture(self.base / "synthetic-test-only.png")
        self.snapshot = self.make_snapshot()

    def tearDown(self):
        self.temp.cleanup()

    def make_snapshot(self, selections=None):
        result = {"schema_version": 1, "contract_version": load_contract()["version"],
                  "expected_page_ids": PAGE_IDS.copy(), "pages": {},
                  "core_verification": {"status": "pass", "checked_at": STAMP, "page_ids": PAGE_IDS.copy()}}
        if selections:
            result["active_sections"] = selections
        for key in PAGE_IDS:
            selected = selections.get(key) if selections else None
            content, bindings = fragments(key, selected)
            result["pages"][key] = {"page_id": PAGE_IDS[key], "fetched_at": STAMP,
                "properties": {"full_width": True, "small_text": False},
                "markdown": render_page(key, content), "source_bindings": bindings,
                "screenshots": [{"path": "synthetic-test-only.png", "page_id": PAGE_IDS[key],
                    "captured_at": STAMP, "viewport": {"width": 1440, "height": 900}, "coverage": "full_page",
                    "observations": [{"check": name, "result": "pass", "note": "Synthetic unittest fixture only"}
                        for name in required_visual_checks(resolve_page_layout(key, selected))]}]}
        return result

    def verify(self, snapshot=None):
        return verify_layout_snapshot(snapshot or self.snapshot, base_dir=self.base, now=NOW)

    def status(self, report, key):
        return next(item["status"] for item in report["checks"] if item["id"] == key)

    def test_all_four_pages_have_independent_structure_visual_and_core_results(self):
        result = self.verify()
        self.assertEqual([], result["issues"])
        self.assertEqual([], result["pending"])
        self.assertTrue(result["structural_complete"])
        self.assertTrue(result["visual_complete"])
        self.assertTrue(result["core_complete"])
        self.assertTrue(result["complete"])
        self.assertIn("not_pixel_similarity", result["visual_method"])

    def test_vertical_list_with_all_sections_and_databases_fails(self):
        import re
        page = self.snapshot["pages"]["home"]
        page["markdown"] = re.sub(r'</?columns>|</?column(?: ratio="[^"]+")?>', '', page["markdown"])
        result = self.verify()
        self.assertFalse(result["structural_complete"])
        self.assertEqual("fail", self.status(result, "home.row.work"))
        self.assertEqual("fail", self.status(result, "home.cards.quick"))
        self.assertTrue(result["core_complete"])
        self.assertFalse(result["layout_complete"])

    def test_ratio_changes_fail_even_when_all_sections_exist(self):
        page = self.snapshot["pages"]["home"]
        page["markdown"] = page["markdown"].replace('ratio="40"', 'ratio="50"').replace('ratio="60"', 'ratio="50"')
        self.assertEqual("fail", self.status(self.verify(), "home.row.work"))

    def test_section_order_is_fixed(self):
        fragments_, _ = fragments("teaching")
        source = self.snapshot["pages"]["teaching"]["markdown"]
        source = source.replace(fragments_["matrix"], "SWAP_SECTION")
        source = source.replace(fragments_["progress"], fragments_["matrix"])
        source = source.replace("SWAP_SECTION", fragments_["progress"])
        self.snapshot["pages"]["teaching"]["markdown"] = source
        self.assertEqual("fail", self.status(self.verify(), "teaching.section_order"))

    def test_moving_management_database_below_toggle_fails(self):
        page = self.snapshot["pages"]["home"]
        import re
        page["markdown"] = re.sub(r'(<details>\s*<summary>오늘·변경 수업 · 관리용</summary>)\s*(<database[^>]*></database>)\s*</details>',
                                  r'\1</details>\n\2', page["markdown"])
        self.assertEqual("fail", self.status(self.verify(), "home.toggle_structure.today"))

    def test_details_does_not_prove_a_toggle_is_collapsed(self):
        observations = self.snapshot["pages"]["home"]["screenshots"][0]["observations"]
        observations[:] = [item for item in observations if item["check"] != "toggle:today:closed"]
        result = self.verify()
        self.assertEqual("pass", self.status(result, "home.toggle_structure.today"))
        self.assertEqual("pending", self.status(result, "home.observation.toggle:today:closed"))
        self.assertFalse(result["layout_complete"])

    def test_missing_settings_and_screenshots_never_pass(self):
        page = self.snapshot["pages"]["home"]
        page.pop("properties")
        page.pop("screenshots")
        page["visual_pass"] = True
        result = self.verify()
        self.assertEqual("pending", self.status(result, "home.full_width"))
        self.assertEqual("pending", self.status(result, "home.screen_coverage"))
        self.assertFalse(result["visual_complete"])

    def test_not_full_width_and_small_text_fail(self):
        self.snapshot["pages"]["home"]["properties"] = {"full_width": False, "small_text": True}
        result = self.verify()
        self.assertEqual("fail", self.status(result, "home.full_width"))
        self.assertEqual("fail", self.status(result, "home.small_text"))

    def test_wrong_page_capture_and_fetch_binding_fail(self):
        page = self.snapshot["pages"]["home"]
        page["screenshots"][0]["page_id"] = PAGE_IDS["classroom"]
        page["markdown"] = f'<page url="https://notion.so/{PAGE_IDS["classroom"]}"><content>{page["markdown"]}</content></page>'
        result = self.verify()
        self.assertEqual("fail", self.status(result, "home.screenshot.1.page"))
        self.assertEqual("fail", self.status(result, "home.fetch_binding"))

    def test_stale_evidence_and_missing_time_are_not_success(self):
        self.snapshot["pages"]["home"]["screenshots"][0]["captured_at"] = (NOW - timedelta(days=2)).isoformat()
        self.snapshot["pages"]["classroom"].pop("fetched_at")
        result = self.verify()
        self.assertEqual("fail", self.status(result, "home.screenshot.1.date"))
        self.assertEqual("pending", self.status(result, "classroom.fetch_date"))

    def test_screenshot_paths_must_exist_and_contain_image_data(self):
        shot = self.snapshot["pages"]["home"]["screenshots"][0]
        shot["path"] = "absent.png"
        self.assertEqual("fail", self.status(self.verify(), "home.screenshot.1.file"))
        (self.base / "absent.png").write_text("This is not a screenshot")
        self.assertEqual("fail", self.status(self.verify(), "home.screenshot.1.file"))

    def test_whole_page_or_top_middle_bottom_are_required(self):
        page = self.snapshot["pages"]["home"]
        page["screenshots"][0]["coverage"] = "top"
        self.assertEqual("pending", self.status(self.verify(), "home.screen_coverage"))
        for coverage in ("middle", "bottom"):
            page["screenshots"].append({**page["screenshots"][0], "coverage": coverage})
        self.assertEqual("pass", self.status(self.verify(), "home.screen_coverage"))

    def test_optional_modules_removed_without_empty_columns(self):
        selections = {key: [section for section, definition in spec["sections"].items() if definition["required"]]
                      for key, spec in load_contract()["pages"].items()}
        result = self.verify(self.make_snapshot(selections))
        self.assertEqual([], result["issues"])
        self.assertTrue(result["complete"])
        page = self.snapshot["pages"]["classroom"]
        page["markdown"] += '\n<columns><column ratio="50"></column><column ratio="50">빈 열 옆</column></columns>'
        self.assertTrue(any("empty_column" in item["id"] for item in self.verify()["issues"]))

    def test_one_optional_school_card_becomes_full_width(self):
        content, _ = fragments("classroom")
        content["school"] = ["학교 업무"]
        self.snapshot["pages"]["classroom"]["markdown"] = render_page("classroom", content)
        self.assertEqual("pass", self.status(self.verify(), "classroom.cards.school"))

    def test_replacing_existing_source_or_linked_database_fails(self):
        page = self.snapshot["pages"]["home"]
        source = page["source_bindings"]["tasks"]["data_source_id"]
        page["markdown"] = page["markdown"].replace("collection://" + source, "collection://00000000000000000000000000009999")
        self.assertEqual("fail", self.status(self.verify(), "home.source_binding.tasks"))

    def test_unknown_source_outside_preserved_inventory_fails(self):
        self.snapshot["expected_data_source_ids"] = {"only_source": "00000000000000000000000000000100"}
        self.assertEqual("fail", self.status(self.verify(), "home.source_inventory"))

    def test_two_different_timetable_blocks_do_not_count_as_shared(self):
        page = self.snapshot["pages"]["home"]
        page["markdown"] = page["markdown"].replace(SYNC_ID, "00000000000000000000000000009998")
        self.assertEqual("fail", self.status(self.verify(), "timetable.shared_source"))

    def test_grid_must_be_seven_columns_and_fit_width(self):
        page = self.snapshot["pages"]["teaching"]
        page["markdown"] = page["markdown"].replace('fit-page-width="true"', 'fit-page-width="false"')
        self.assertEqual("fail", self.status(self.verify(), "timetable.week_grid"))

    def test_nav_must_link_all_four_bound_pages(self):
        page = self.snapshot["pages"]["home"]
        page["markdown"] = page["markdown"].replace('https://notion.so/' + PAGE_IDS["classroom"],
                                                    'https://notion.so/' + PAGE_IDS["home"])
        self.assertEqual("fail", self.status(self.verify(), "home.nav_targets"))

    def test_menu_link_with_annual_title_does_not_substitute_for_profile(self):
        page = self.snapshot["pages"]["home"]
        page["markdown"] = page["markdown"].replace('>이동</mention-page>', '>2026학년도</mention-page>')
        page["markdown"] = page["markdown"].replace('**2026학년도** · 가상 교사 · 과학', '')
        self.assertEqual("fail", self.status(self.verify(), "home.section.intro"))

    def test_expected_synced_source_rejects_replacing_both_original_and_reference(self):
        self.snapshot["expected_synced_block_id"] = SYNC_ID
        for key in ("home", "teaching"):
            page = self.snapshot["pages"][key]
            page["markdown"] = page["markdown"].replace(SYNC_ID, "00000000000000000000000000009998")
        self.assertEqual("fail", self.status(self.verify(), "timetable.shared_source"))

    def test_missing_core_verification_does_not_claim_install_complete(self):
        self.snapshot.pop("core_verification")
        result = self.verify()
        self.assertIsNone(result["core_complete"])
        self.assertTrue(result["layout_complete"])
        self.assertFalse(result["complete"])

    def test_public_example_can_never_be_completion_evidence(self):
        example = json.loads((Path(__file__).parents[1] / "examples/layout-evidence.example.json").read_text())
        report = self.verify(example)
        self.assertFalse(report["complete"])
        self.assertEqual("fail", self.status(report, "snapshot.actual"))

    def test_verifier_does_not_return_or_change_page_contents(self):
        before = copy.deepcopy(self.snapshot)
        report = self.verify()
        self.assertEqual(before, self.snapshot)
        self.assertNotIn("가상 교사", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
