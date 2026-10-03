"""Read-only acceptance checks for a captured, actual Notion layout.

Enhanced Markdown proves structure, not page settings or a toggle's open state.
Visual completion therefore additionally requires local screenshots and explicit
observations made while inspecting the bound pages. This is an evidence checker,
not an image-similarity model, and never reads or returns student record contents.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
import struct


MAX_EVIDENCE_AGE = timedelta(hours=24)
_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)


def _identity(value):
    matches = _UUID.findall(str(value or ""))
    return matches[-1].replace("-", "").lower() if matches else None


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _label(value):
    value = re.sub(r"\*\*|__|\\", "", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"^[^\w]+", "", value).strip()


@dataclass
class _Node:
    tag: str
    order: int
    attrs: dict = field(default_factory=dict)
    text: str = ""
    parent: object = None
    children: list = field(default_factory=list)

    def ancestors(self, tag):
        result, node = [], self.parent
        while node is not None:
            if node.tag == tag:
                result.append(node)
            node = node.parent
        return result

    def descendants(self, tag=None):
        result = []
        for child in self.children:
            if tag is None or child.tag == tag:
                result.append(child)
            result.extend(child.descendants(tag))
        return result

    def contents(self):
        return self.text + " ".join(child.contents() for child in self.children)


class _MarkdownTree(HTMLParser):
    """Parse the structural HTML subset without executing any embedded content."""

    def __init__(self, markdown):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", 0)
        self.stack = [self.root]
        self.nodes = []
        self.errors = []
        # Fetch envelopes contain properties/ancestors that are not page blocks.
        content = re.search(r"<content>([\s\S]*?)</content>", markdown)
        self.feed(content.group(1) if content else markdown)
        self.close()
        if len(self.stack) != 1:
            self.errors.append("unclosed structural tags")

    def _add(self, tag, attrs=None, text=""):
        node = _Node(tag, len(self.nodes) + 1, attrs or {}, text, self.stack[-1])
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, dict(attrs))
        if tag not in {"br", "col", "hr", "img", "input"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, dict(attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                if index != len(self.stack) - 1:
                    self.errors.append("mismatched structural tags")
                del self.stack[index:]
                return
        if tag not in {"br", "col", "hr", "img", "input"}:
            self.errors.append("unexpected closing tag")

    def handle_data(self, data):
        for line in data.splitlines():
            if not line.strip():
                continue
            heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
            self._add("heading" if heading else "text", text=heading.group(2) if heading else line.strip())


def _matches(node, labels):
    value = _label(node.contents())
    return any(value == label or value.startswith(label + " · ") or value.startswith(label + " ·")
               for label in labels)


def _locate(tree, key, definition):
    labels = [_label(definition.get("heading", definition["title"]))]
    labels += [_label(value) for value in definition.get("heading_aliases", [])]
    if key == "nav":
        return [node for node in tree.nodes if node.tag == "callout"
                and len(node.descendants("mention-page")) >= 4]
    if key == "intro":
        return [node for node in tree.nodes if node.tag == "text" and
                re.search(r"\d{4}학년도", node.text) and not node.ancestors("table")
                and not node.ancestors("mention-page") and not node.ancestors("summary")][:1]
    if key in {"updates", "forms"}:
        # The reference keeps these compact as native/Markdown links, not rows
        # with an extra heading. Match the link label, never the private URL.
        link_labels = labels + (["업데이트 확인"] if key == "updates" else ["양식 모음"])
        candidates = []
        for node in tree.nodes:
            if node.tag == "mention-page" and _label(node.contents()) in link_labels:
                if not node.ancestors("callout") and not node.ancestors("details"):
                    candidates.append(node)
            elif node.tag == "text" and not node.ancestors("callout") and not node.ancestors("details"):
                if any(_label(label) in link_labels for label in re.findall(r"\[([^\]]+)\]\([^\s)]+\)", node.text)):
                    candidates.append(node)
        if candidates:
            return candidates
    if definition.get("toggle"):
        return [node.parent for node in tree.nodes if node.tag == "summary" and _matches(node, labels)]
    headings = [node for node in tree.nodes if node.tag == "heading" and _matches(node, labels)]
    if headings:
        return headings
    # Callout labels may be bold and include a date; its body is not a heading.
    callouts = []
    for node in tree.nodes:
        if node.tag == "callout":
            texts = node.descendants("text")
            if texts and _matches(texts[0], labels):
                callouts.append(node)
    return callouts


def required_visual_checks(page_layout):
    """Names a human/browser inspector must record against actual screenshots."""
    checks = ["full_width", "small_text", "columns", "section_order", "no_horizontal_overflow"]
    checks.extend("toggle:" + key + ":closed" for key, value in page_layout["sections"].items()
                  if value.get("toggle") and value.get("default_open") is False)
    return checks


def _image_size(path):
    """Read dimensions from PNG/JPEG headers without loading or changing pixels."""
    with path.open("rb") as stream:
        data = stream.read(24)
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) == 24 and data[12:16] == b"IHDR":
            stream.seek(0, 2)
            if stream.tell() < 57:
                return None
            stream.seek(-12, 2)
            if stream.read(12) != b"\0\0\0\0IEND\xaeB`\x82":
                return None
            return struct.unpack(">II", data[16:24])
        if not data.startswith(b"\xff\xd8"):
            return None
        stream.seek(2)
        while True:
            byte = stream.read(1)
            if not byte:
                return None
            if byte != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker or marker in (b"\xd9", b"\xda"):
                return None
            if marker in (b"\xd8", b"\x01") or 0xD0 <= marker[0] <= 0xD7:
                continue
            raw_length = stream.read(2)
            if len(raw_length) != 2:
                return None
            length = int.from_bytes(raw_length, "big")
            if length < 2:
                return None
            if marker[0] in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                data = stream.read(5)
                if len(data) != 5:
                    return None
                height, width = struct.unpack(">HH", data[1:])
                return width, height
            stream.seek(length - 2, 1)


def verify_layout_snapshot(snapshot, base_dir=None, now=None):
    """Check JSON evidence against the common contract; never contact Notion.

    ``base_dir`` resolves screenshot paths (normally the evidence file's parent).
    All four page bindings are required. Missing observations stay ``pending``;
    observed mismatches are ``fail``. A report cannot turn an example into a real
    successful installation. ``core_complete`` is independently attested and is
    never inferred from the layout, screenshots, or a list of created databases.
    """
    from .layout_contract import load_contract, resolve_page_layout

    contract = load_contract()
    ratio_tolerance = contract.get("rules", {}).get("column_ratio_tolerance_percentage_points", 0.5)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now에는 시간대가 필요합니다.")
    now = now.astimezone(timezone.utc)
    base_dir = Path(base_dir or ".").resolve()
    checks = []

    def add(key, category, status, message):
        checks.append({"id": key, "category": category, "status": status, "message": message})

    def freshness(key, value, category):
        timestamp = _date(value)
        status = ("pending" if timestamp is None else "pass" if
                  -timedelta(minutes=5) <= now - timestamp <= MAX_EVIDENCE_AGE else "fail")
        add(key, category, status, "24시간 이내의 시간대 포함 실제 확인 시각")
        return status == "pass"

    if not isinstance(snapshot, dict):
        raise ValueError("배치 증거는 JSON 객체여야 합니다.")
    add("snapshot.schema", "structure", "pass" if snapshot.get("schema_version") == 1 else "fail",
        "배치 증거 형식 버전 1")
    add("snapshot.contract", "structure", "pass" if snapshot.get("contract_version") == contract["version"] else "fail",
        "현재 공통 배치 명세 버전과 일치")
    add("snapshot.actual", "structure", "fail" if snapshot.get("example") is True else "pass",
        "공개 예시 파일은 실제 설치 완료 증거가 아님")
    expected_ids = snapshot.get("expected_page_ids", {})
    pages = snapshot.get("pages", {})
    active = snapshot.get("active_sections", {})
    if not all(isinstance(value, dict) for value in (expected_ids, pages, active)):
        raise ValueError("expected_page_ids, pages, active_sections는 JSON 객체여야 합니다.")
    expected_sources = snapshot.get("expected_data_source_ids")
    if expected_sources is not None:
        expected_sources = {_identity(value) for value in expected_sources.values()} if isinstance(expected_sources, dict) else set()
        if None in expected_sources or not expected_sources:
            add("snapshot.sources", "structure", "fail", "원본 데이터 소스 목록의 ID가 올바르지 않음")
    parsed_pages = {}
    capture_viewports = set()

    for page_key in contract["pages"]:
        prefix = page_key + "."
        page = pages.get(page_key, {}) if isinstance(pages, dict) else {}
        if not isinstance(page, dict):
            raise ValueError("pages의 각 페이지는 JSON 객체여야 합니다.")
        expected_id = _identity(expected_ids.get(page_key)) if isinstance(expected_ids, dict) else None
        page_id = _identity(page.get("page_id"))
        bound = bool(page_id and expected_id and page_id == expected_id)
        add(prefix + "page_binding", "structure", "pending" if not page_id or not expected_id else "pass" if bound else "fail",
            "설치 기록의 대상 페이지와 읽어 온 페이지 ID 일치")
        freshness(prefix + "fetch_date", page.get("fetched_at"), "structure")
        try:
            page_layout = resolve_page_layout(page_key, active.get(page_key))
        except (KeyError, TypeError, ValueError):
            add(prefix + "selection", "structure", "fail", "선택한 섹션 목록이 공통 명세와 일치하지 않음")
            continue
        sections = page_layout["sections"]
        properties = page.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        for name, expected in (("full_width", True), ("small_text", False)):
            value = properties.get(name)
            add(prefix + name, "visual", "pending" if value is None else "pass" if value is expected else "fail",
                "페이지 설정의 실제 읽기 결과: " + name + "=" + str(expected).lower())
        markdown = page.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            add(prefix + "markdown", "structure", "pending", "실제 페이지를 다시 읽은 enhanced Markdown 필요")
            tree = None
        else:
            tree = _MarkdownTree(markdown)
            add(prefix + "markdown", "structure", "fail" if tree.errors else "pass", "실제 페이지 구조의 태그 중첩 확인")
            envelope = re.search(r'<page\b[^>]*\burl="([^"]+)"', markdown)
            if "<content>" in markdown and envelope:
                add(prefix + "fetch_binding", "structure", "pass" if _identity(envelope.group(1)) == page_id else "fail",
                    "fetch 응답 표지의 페이지 ID와 증거의 페이지 ID 일치")
            located = {}
            for key, definition in sections.items():
                candidates = _locate(tree, key, definition)
                status = "pass" if len(candidates) == 1 else "fail"
                add(prefix + "section." + key, "structure", status, "공통 명세 섹션이 정해진 위치에 한 번 존재")
                if len(candidates) == 1:
                    located[key] = candidates[0]
            ordered = sorted(located.items(), key=lambda item: item[1].order)
            spans = {}
            for index, (key, node) in enumerate(ordered):
                end = ordered[index + 1][1].order if index + 1 < len(ordered) else len(tree.nodes) + 1
                spans[key] = [candidate for candidate in tree.nodes if node.order <= candidate.order < end]
            expected_order = [key for row in page_layout["rows"] for column in row["columns"] for key in column["sections"]]
            add(prefix + "section_order", "structure", "pass" if [key for key, _ in ordered] == expected_order else "fail",
                "섹션을 행 → 열 → 섹션 순서로 읽었을 때 공통 명세와 일치")
            for row in page_layout["rows"]:
                row_key = prefix + "row." + row["id"]
                groups, valid = [], True
                for column in row["columns"]:
                    nodes = [located.get(key) for key in column["sections"]]
                    if any(node is None for node in nodes):
                        valid = False
                        continue
                    valid = valid and not any(node.ancestors("details") for node in nodes)
                    ancestors = [node.ancestors("column") for node in nodes]
                    if len(row["columns"]) == 1:
                        valid = valid and not any(ancestors)
                    else:
                        if any(not items for items in ancestors):
                            valid = False
                            continue
                        parent = ancestors[0][-1]
                        valid = valid and all(items[-1] is parent for items in ancestors)
                        groups.append(parent)
                        try:
                            valid = valid and abs(float(parent.attrs.get("ratio", "nan")) - column["ratio"]) <= ratio_tolerance
                        except (TypeError, ValueError):
                            valid = False
                if len(row["columns"]) > 1:
                    valid = valid and len(groups) == len(row["columns"])
                    if groups:
                        siblings = [node for node in groups[0].parent.children if node.tag == "column"]
                        valid = valid and all(node.parent is groups[0].parent for node in groups) and siblings == groups
                add(row_key, "structure", "pass" if valid else "fail", "행의 열 개수·순서·상대 폭·같은 열의 섹션 배치 확인")
            if "nav" in located:
                targets = {_identity(node.attrs.get("url")) for node in located["nav"].descendants("mention-page")}
                wanted = {_identity(value) for value in expected_ids.values()}
                add(prefix + "nav_targets", "structure", "pass" if len(wanted) == 4 and None not in wanted and wanted <= targets else "fail",
                    "공통 이동 링크가 같은 수첩의 네 페이지를 가리킴")
            bindings = page.get("source_bindings", {})
            if not isinstance(bindings, dict):
                bindings = {}
                add(prefix + "source_bindings", "structure", "fail", "원본 연결 증거 형식 오류")
            if set(bindings) - set(sections):
                add(prefix + "source_bindings.selection", "structure", "fail", "선택하지 않은 섹션의 원본 연결 증거가 있음")
            for key, node in located.items():
                definition = sections[key]
                content = spans[key]
                databases = [item for item in content if item.tag == "database"]
                if "database_blocks" in definition:
                    add(prefix + "database." + key, "structure",
                        "pass" if len(databases) == definition["database_blocks"] else "fail",
                        "해당 섹션의 연결 데이터베이스 블록 개수 확인")
                if definition.get("toggle"):
                    valid = node.tag == "details" and all(node in item.ancestors("details") for item in databases)
                    add(prefix + "toggle_structure." + key, "structure", "pass" if valid else "fail",
                        "관리용 내용이 해당 접기 블록의 실제 자식임 (접힘 상태는 별도 UI 확인)")
                if definition.get("cards"):
                    card_spec = definition["cards"]
                    groups = [item for item in content if item.tag == "columns"]
                    single_cards = [item for item in content if item.tag == "callout"]
                    valid = len(groups) == 1 or (not groups and card_spec["min"] == 1 and len(single_cards) == 1)
                    if groups:
                        columns = [item for item in groups[0].children if item.tag == "column"]
                        valid = valid and card_spec["min"] <= len(columns) <= card_spec["max"]
                        desired = card_spec["ratios"][:len(columns)]
                        for index, column in enumerate(columns):
                            try:
                                wanted = 100 * desired[index] / sum(desired)
                                valid = valid and abs(float(column.attrs.get("ratio", "nan")) - wanted) <= ratio_tolerance
                            except (IndexError, TypeError, ValueError, ZeroDivisionError):
                                valid = False
                            valid = valid and bool(column.contents().strip() or column.descendants("mention-page"))
                    add(prefix + "cards." + key, "structure", "pass" if valid else "fail", "카드의 개수·균등 열 폭·빈 열 없음 확인")
                if key in bindings:
                    binding = bindings[key]
                    source_id = _identity(binding.get("data_source_id")) if isinstance(binding, dict) else _identity(binding)
                    actual_ids = [_identity(item.attrs.get("data-source-url")) for item in databases]
                    valid = bool(source_id and actual_ids and all(value == source_id for value in actual_ids))
                    if isinstance(binding, dict) and binding.get("database_id"):
                        valid = valid and all(_identity(item.attrs.get("url")) == _identity(binding["database_id"]) for item in databases)
                    add(prefix + "source_binding." + key, "structure", "pass" if valid else "fail", "기존 원본·연결 데이터베이스 ID 보존")
            for column in (node for node in tree.nodes if node.tag == "column"):
                nonempty = bool(column.contents().strip() or column.descendants("database") or column.descendants("mention-page"))
                if not nonempty:
                    add(prefix + "empty_column." + str(column.order), "structure", "fail", "선택하지 않은 기능을 빈 열로 남기지 않음")
            if expected_sources is not None:
                actual = {_identity(node.attrs.get("data-source-url")) for node in tree.nodes if node.tag == "database"}
                add(prefix + "source_inventory", "structure", "pass" if actual <= expected_sources else "fail",
                    "모든 연결 DB가 설치 기록에 있는 기존 원본을 사용")
            if page_key == "home" and "document_storage" in located:
                storage = located["document_storage"].order
                add(prefix + "child_pages", "structure", "pass" if all(node.order > storage for node in tree.nodes if node.tag == "page") else "fail",
                    "하위 페이지가 홈 상단 대신 문서 보관실 아래 위치")
            parsed_pages[page_key] = (tree, located, spans)

        observations, coverage = {}, set()
        screenshots = page.get("screenshots", [])
        if not isinstance(screenshots, list):
            screenshots = []
        for index, shot in enumerate(screenshots):
            shot_key = prefix + "screenshot." + str(index + 1)
            if not isinstance(shot, dict):
                add(shot_key, "visual", "fail", "캡처 증거 형식 오류")
                continue
            capture_fresh = freshness(shot_key + ".date", shot.get("captured_at"), "visual")
            shot_bound = bool(bound and _identity(shot.get("page_id")) == page_id)
            add(shot_key + ".page", "visual", "pass" if shot_bound else "fail", "캡처한 페이지가 검증 대상 페이지와 일치")
            path = shot.get("path")
            valid_file = False
            viewport = shot.get("viewport", {})
            try:
                size = _image_size(base_dir / path) if isinstance(path, str) and path else None
                valid_file = bool(size and size[0] >= 640 and size[1] >= 360)
            except (OSError, ValueError, TypeError):
                pass
            add(shot_key + ".file", "visual", "pass" if valid_file else "fail", "읽을 수 있는 실제 PNG/JPEG 화면 캡처 파일 필요")
            valid_viewport = (isinstance(viewport, dict) and type(viewport.get("width")) is int and
                              type(viewport.get("height")) is int and viewport["width"] >= 1280 and viewport["height"] >= 720)
            add(shot_key + ".viewport", "visual", "pass" if valid_viewport else "fail", "1280×720 이상 데스크톱에서 확인한 화면과 실제 뷰포트 크기 기록")
            if valid_viewport:
                capture_viewports.add((viewport["width"], viewport["height"]))
            valid = capture_fresh and shot_bound and valid_file and valid_viewport
            if valid:
                if isinstance(shot.get("coverage"), str):
                    coverage.add(shot["coverage"])
                shot_observations = shot.get("observations", [])
                if not isinstance(shot_observations, list):
                    shot_observations = []
                for item in shot_observations:
                    if isinstance(item, dict) and item.get("result") in ("pass", "fail") and isinstance(item.get("note"), str) and item["note"].strip():
                        observations.setdefault(item.get("check"), []).append(item["result"])
        add(prefix + "screen_coverage", "visual", "pass" if "full_page" in coverage or {"top", "middle", "bottom"} <= coverage else "pending",
            "전체 페이지 캡처 또는 상단·중간·하단 캡처가 모두 필요")
        for name in required_visual_checks(page_layout):
            values = observations.get(name, [])
            status = "fail" if "fail" in values else "pass" if values else "pending"
            add(prefix + "observation." + name, "visual", status, "실제 화면을 보고 기록한 관찰 필요: " + name)

    # The displayed timetable is one shared object, not two look-alike tables.
    if "home" in parsed_pages and "teaching" in parsed_pages:
        home_nodes = parsed_pages["home"][2].get("matrix", [])
        teaching_nodes = parsed_pages["teaching"][2].get("matrix", [])
        references = [node for node in home_nodes if node.tag == "synced_block_reference"]
        sources = [node for node in teaching_nodes if node.tag == "synced_block"]
        same = (len(references) == len(sources) == 1 and bool(_identity(sources[0].attrs.get("url")))
                and _identity(references[0].attrs.get("url")) == _identity(sources[0].attrs.get("url")))
        expected_synced = snapshot.get("expected_synced_block_id")
        if expected_synced is not None:
            same = same and bool(_identity(expected_synced)) and _identity(sources[0].attrs.get("url")) == _identity(expected_synced)
        add("timetable.shared_source", "structure", "pass" if same else "fail", "홈 격자가 교과 페이지의 동일한 동기화 원본을 참조")
        tables = [node for node in teaching_nodes if node.tag == "table"]
        valid = len(tables) == 1
        if tables:
            rows = tables[0].descendants("tr")
            headers = rows[0].descendants("td") if rows else []
            names = [_label(node.contents()).replace(" ", "") for node in headers]
            expected = [value.replace(" ", "") for value in contract["timetable"]["columns"]]
            valid = (valid and tables[0].attrs.get("fit-page-width") == "true"
                     and tables[0].attrs.get("header-row") == "true" and len(names) == len(expected)
                     and all(name.startswith(wanted) for name, wanted in zip(names, expected)))
        add("timetable.week_grid", "structure", "pass" if valid else "fail", "전폭 표의 교시·수업 시간·월~금 7열과 헤더 확인")

    add("visual.viewport_consistency", "visual", "fail" if len(capture_viewports) > 1 else "pass" if capture_viewports else "pending",
        "네 페이지를 같은 브라우저 뷰포트 크기로 확인")

    core = snapshot.get("core_verification", {})
    core_complete = None
    if isinstance(core, dict) and core:
        dated = freshness("core.date", core.get("checked_at"), "core")
        ids_match = core.get("page_ids") == expected_ids and bool(expected_ids)
        add("core.page_binding", "core", "pass" if ids_match else "fail", "기능 검증과 배치 검증의 대상 수첩 일치")
        status = core.get("status")
        add("core.result", "core", status if status in ("pass", "fail") else "pending", "별도로 수행한 원본·관계·보기 기능 검증 결과")
        core_complete = dated and ids_match and status == "pass"
    else:
        add("core.result", "core", "pending", "원본·관계·보기 기능 검증 결과는 별도 기록 필요")
    structural_complete = all(item["status"] == "pass" for item in checks if item["category"] == "structure")
    visual_complete = all(item["status"] == "pass" for item in checks if item["category"] == "visual")
    layout_complete = structural_complete and visual_complete
    issues = [item for item in checks if item["status"] == "fail"]
    pending = [item for item in checks if item["status"] == "pending"]
    return {"contract_version": contract["version"], "core_complete": core_complete,
            "structural_complete": structural_complete, "visual_complete": visual_complete,
            "layout_complete": layout_complete, "complete": core_complete is True and layout_complete,
            "status": "fail" if issues else "pending" if pending else "pass",
            "visual_method": "local_screenshots_and_recorded_ui_observations; not_pixel_similarity",
            "checks": checks, "issues": issues, "pending": pending}
