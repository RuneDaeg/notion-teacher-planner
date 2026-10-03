"""Read-only, deterministic layout planning for native Notion pages.

The returned Markdown is a *plan*, never proof of installation. Page width and
collapsed toggles must be set and read back in Notion, then inspected in the UI.
Section strings are existing complete Markdown fragments. Native URLs, database
blocks, and synced-block references are retained; only structural indentation is
added. A card section may instead contain a list of individual callout strings.
No API client, Notion writes, real installation IDs, or sample records live here.
"""
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
import json
from pathlib import Path


class LayoutContractError(ValueError):
    """The supplied content cannot be placed without loss or a silent fallback."""


def load_contract():
    """Load a fresh public contract; callers cannot mutate the next result."""
    return json.loads(Path(__file__).with_name('layout_contract.json').read_text(encoding='utf-8'))


def _page(page_key):
    try:
        return load_contract()['pages'][page_key]
    except KeyError:
        raise LayoutContractError('unknown_page: ' + str(page_key)) from None


def _ratios(values):
    """Normalize without cumulative decimal drift; last column fills the row."""
    total = sum(values)
    ratios = [round(100 * value / total, 6) for value in values]
    ratios[-1] = round(100 - sum(ratios[:-1]), 6)
    return ratios


def resolve_page_layout(page_key, active_sections=None):
    """Resolve ordered rows for supplied/selected sections, rejecting data loss.

    With no section list, returns all contract sections. Optional empty columns
    disappear and the remaining ratio is normalized; a singleton becomes 100%.
    Unknown keys and absent required sections are errors, never silently omitted.
    """
    page = _page(page_key)
    if active_sections is None:
        active = set(page['sections'])
    else:
        if isinstance(active_sections, str):
            raise LayoutContractError('active_sections must be a collection of section keys')
        active = set(active_sections)
    unknown = active - page['sections'].keys()
    if unknown:
        raise LayoutContractError('unknown_sections: ' + ', '.join(sorted(unknown)))
    missing = {key for key, value in page['sections'].items()
               if value['required'] and key not in active}
    if missing:
        raise LayoutContractError('required_missing: ' + ', '.join(sorted(missing)))
    rows = []
    for original in page['rows']:
        columns = []
        for column in original['columns']:
            keys = [key for key in column['sections'] if key in active]
            if keys:
                columns.append({**column, 'sections': keys})
        if not columns:
            continue
        for column, ratio in zip(columns, _ratios([c['ratio'] for c in columns])):
            column['ratio'] = ratio
        rows.append({'id': original['id'], 'columns': columns})
    page['rows'] = rows
    page['sections'] = {key: value for key, value in page['sections'].items() if key in active}
    return page


def active_sections_for_config(config, page_key):
    """Resolve module/form choices without enabling integrations or extra modules.

    Status and update callouts are not fabricated from configuration. Add those
    optional keys only when an existing, populated callout is being retained.
    Meals is always a display slot; an unconnected placeholder is valid content.
    """
    from .dashboard import dashboard_config
    from .forms import selected_forms
    from .model import dashboard_views, selected

    page = _page(page_key)
    keys = {key for key, value in page['sections'].items() if value['required']}
    keys.update(view['section'] for view in dashboard_views(config)
                if view['workspace_page'] == page_key)
    if page_key == 'home' and selected_forms(config):
        keys.add('forms')
    if page_key == 'classroom':
        sources = {item['key'] for item in selected(config)[0]}
        links = dashboard_config(config)['links']
        if sources.intersection({'staff', 'accounts', 'meetings'}) or any(links.values()):
            keys.add('school')
    resolved = resolve_page_layout(page_key, keys)
    return [key for row in resolved['rows'] for column in row['columns']
            for key in column['sections']]


def _indent(text):
    return '\n'.join('\t' + line if line else '' for line in text.split('\n'))


def _number(value):
    return f'{value:.6f}'.rstrip('0').rstrip('.')


def _columns(contents, ratios):
    if len(contents) == 1:
        return contents[0]
    columns = [f'<column ratio="{_number(ratio)}">\n{_indent(content)}\n</column>'
               for content, ratio in zip(contents, ratios)]
    return '<columns>\n' + '\n'.join(_indent(column) for column in columns) + '\n</columns>'


class _ColumnInspector(HTMLParser):
    """Inspect only native column structure; opaque payload content is untouched."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.groups = []
        self.current = None
        self.databases = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'database':
            self.databases += 1
        if tag == 'columns':
            if self.depth == 0:
                self.current = []
                self.groups.append(self.current)
            self.depth += 1
        elif tag == 'column' and self.depth == 1:
            try:
                self.current.append(float(dict(attrs).get('ratio', 'nan')))
            except ValueError:
                self.current.append(float('nan'))

    def handle_endtag(self, tag):
        if tag == 'columns':
            self.depth -= 1


def _cards(spec, value):
    definition = spec['cards']
    if isinstance(value, str):
        # Existing complete sections can retain their heading and native blocks.
        # A plain line of links is never accepted as a card row.
        parser = _ColumnInspector()
        parser.feed(value)
        if len(parser.groups) != 1:
            raise LayoutContractError('card_layout_missing: ' + spec['title'])
        ratios = parser.groups[0]
        count = len(ratios)
        if not definition['min'] <= count <= definition['max']:
            raise LayoutContractError('card_count: ' + spec['title'])
        expected = _ratios(definition['ratios'][:count])
        if any(not abs(actual - wanted) <= 0.00001 for actual, wanted in zip(ratios, expected)):
            raise LayoutContractError('card_ratios: ' + spec['title'])
        return value.strip('\n')
    count = len(value)
    if not definition['min'] <= count <= definition['max']:
        raise LayoutContractError('card_count: ' + spec['title'])
    contents = []
    for item in value:
        if not item.lstrip().startswith('<callout'):
            item = '<callout color="gray_bg">\n' + _indent(item.strip('\n')) + '\n</callout>'
        contents.append(item.strip('\n'))
    return '## ' + spec['title'] + '\n' + _columns(contents, _ratios(definition['ratios'][:count]))


def _section(spec, value):
    if 'cards' in spec:
        return _cards(spec, value)
    if not isinstance(value, str):
        raise LayoutContractError('Only card sections accept lists: ' + spec['title'])
    text = value.strip('\n')
    if spec.get('database_blocks'):
        parser = _ColumnInspector()
        parser.feed(text)
        if parser.databases != spec['database_blocks']:
            raise LayoutContractError('database_block_count: ' + spec['title']
                                      + '; all views must remain tabs of one linked database')
    if spec.get('block_style') == 'callout':
        if not text.lstrip().startswith('<callout'):
            icon = '🧭' if spec.get('locator', {}).get('kind') == 'navigation_callout' else '💡'
            text = '<callout icon="' + icon + '" color="gray_bg">\n' + _indent(text) + '\n</callout>'
    if spec.get('toggle'):
        # Do not invent an "open=false" attribute: Notion does not support it.
        # A supplied complete toggle retains its native payload and summary.
        if not text.lstrip().startswith('<details'):
            text = '<details>\n<summary>' + spec['title'] + '</summary>\n' + _indent(text) + '\n</details>'
    return text


def render_page(page_key, sections):
    """Render one page from exact supplied native section fragments.

    Strings include their own headings. Card lists get a contract heading and
    fixed columns. An existing complete card section string must already have
    the required columns/ratios. No database is recreated, and no view is emitted
    separately from its supplied native database block. This pure renderer does
    not invent page/database references or apply changes to Notion.
    """
    if not isinstance(sections, Mapping):
        raise LayoutContractError('sections must be a mapping')
    known = _page(page_key)['sections']
    unknown = set(sections) - known.keys()
    if unknown:
        raise LayoutContractError('unknown_sections: ' + ', '.join(sorted(unknown)))
    active = {}
    for key, value in sections.items():
        if isinstance(value, str):
            if value.strip():
                active[key] = value
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise LayoutContractError('card_content: ' + key)
            if value:
                active[key] = value
        else:
            raise LayoutContractError('section_content: ' + key)
    page = resolve_page_layout(page_key, active)
    rows = []
    for row in page['rows']:
        contents = ['\n'.join(_section(page['sections'][key], active[key])
                              for key in column['sections']) for column in row['columns']]
        rows.append(_columns(contents, [column['ratio'] for column in row['columns']]))
    return '\n'.join(rows) + '\n'
