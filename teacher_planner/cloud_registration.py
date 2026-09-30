"""Prepare a public-identifier-only cloud enrollment link without connecting."""
import base64
import hashlib
import ipaddress
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


MANIFEST_KEYS = frozenset({
    'version', 'office_code', 'school_code', 'school_name', 'academic_year',
    'root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id',
})
ID_KEYS = ('root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id')
SCHOOL_KEYS = ('office_code', 'school_code', 'school_name', 'academic_year')
MAX_MANIFEST_BYTES = 4096
MAX_STATE_BYTES = 16 * 1024 * 1024
_ID = re.compile(r'(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z')
_SECRET = re.compile(r'(?i)(?:bearer\s|(?:ntn|nrt|secret|sk)[_-]|(?:api[_-]?key|access[_-]?token|password)\s*[:=])')


class CloudRegistrationError(ValueError):
    """A safe message that never includes the rejected input."""


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CloudRegistrationError('Notion 식별자는 URL이 아닌 UUID여야 합니다.')
    identifier = UUID(value)
    if identifier.int == 0:
        raise CloudRegistrationError('비어 있는 Notion 식별자는 사용할 수 없습니다.')
    return str(identifier)


def validate_manifest(value):
    """Validate manifest v1 exactly; discard nothing from an explicit manifest."""
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS:
        raise CloudRegistrationError('연결 정보는 manifest v1의 정해진 9개 필드만 포함해야 합니다.')
    if type(value['version']) is not int or value['version'] != 1:
        raise CloudRegistrationError('지원하는 연결 정보 버전은 1입니다.')
    if not isinstance(value['office_code'], str) or not re.fullmatch(r'[A-Z][0-9]{2}', value['office_code']):
        raise CloudRegistrationError('NEIS 교육청 코드 형식을 확인하세요.')
    if not isinstance(value['school_code'], str) or not re.fullmatch(r'[0-9]{7}', value['school_code']):
        raise CloudRegistrationError('NEIS 표준학교코드는 숫자 7자리 문자열이어야 합니다.')
    name = value['school_name']
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 200 or len(name) > 200
            or any(unicodedata.category(char).startswith('C') for char in name)
            or _SECRET.search(name) or any(char in name for char in '<>')
            or '://' in name):
        raise CloudRegistrationError('학교명에는 공개 학교 이름만 넣으세요.')
    year = value['academic_year']
    if type(year) is not int or not 1900 <= year <= 9998:
        raise CloudRegistrationError('학년도는 지원 범위의 정수여야 합니다.')
    result = {'version': 1, 'office_code': value['office_code'], 'school_code': value['school_code'],
              'school_name': name.strip(), 'academic_year': year}
    result.update((key, _identifier(value[key])) for key in ID_KEYS)
    if len({result[key] for key in ID_KEYS}) != len(ID_KEYS):
        raise CloudRegistrationError('페이지·데이터 소스·급식·상태 블록 식별자를 구분하세요.')
    if len(_json_bytes(result)) > MAX_MANIFEST_BYTES:
        raise CloudRegistrationError('연결 정보가 허용 크기를 넘었습니다.')
    return result


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _service_origin(value):
    message = '클라우드 서비스 주소는 경로·인증정보 없는 공개 HTTPS origin이어야 합니다.'
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(char.isspace() or ord(char) < 32 for char in value)
            or any(char in value for char in '\\?#')):
        raise CloudRegistrationError(message)
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.path or parts.netloc.endswith(':')
                or parts.username is not None or parts.password is not None):
            raise ValueError
        host = parts.hostname.encode('idna').decode('ascii').lower()
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if host.endswith('.') or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            if (len(labels) < 2 or len(host) > 253
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                    or labels[-1].isdigit()):
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
            if address.version == 6:
                host = '[' + host + ']'
        return 'https://' + host + (':' + str(port) if port is not None else '')
    except (ValueError, UnicodeError):
        raise CloudRegistrationError(message) from None


def enrollment_url(base_url, manifest):
    """Use a URL fragment so the initial HTTP request excludes the manifest."""
    origin = _service_origin(base_url)
    encoded = base64.urlsafe_b64encode(_json_bytes(validate_manifest(manifest))).decode('ascii').rstrip('=')
    return origin + '/connect#' + encoded


def build_manifest(state, school_context=None, *, status_block_id=None):
    """Project recorded REST IDs and existing public school context only.

    MCP records deliberately have a different shape and must supply an explicit
    manifest instead. No Journal, token, workspace identity or student data is
    exported, and this function never changes the installation state.
    """
    if (not isinstance(state, dict) or state.get('route') not in (None, 'rest')
            or state.get('complete') is not True or not isinstance(state.get('config'), dict)
            or not isinstance(state.get('objects'), dict) or not isinstance(state.get('databases'), dict)):
        raise CloudRegistrationError('완료된 REST 설치 상태가 필요합니다. MCP 설치는 --manifest를 사용하세요.')
    if state.get('pending'):
        raise CloudRegistrationError('완료 여부가 불확실한 설치 작업을 먼저 확인하세요.')
    try:
        config = state['config']
        school = school_context if school_context is not None else config.get('school')
        if not isinstance(school, dict):
            raise CloudRegistrationError('설치 담당자가 기존 학교 설정 또는 NEIS 조회 JSON을 --school-config로 지정하세요.')
        year = config['academic_year']
        if 'academic_year' in school and (type(school['academic_year']) is not int or school['academic_year'] != year):
            raise CloudRegistrationError('학교 설정과 설치 학년도가 다릅니다.')
        cloud = state.get('cloud_sync', {})
        recorded_status = cloud.get('status_block_id')
        if recorded_status and status_block_id and _identifier(recorded_status) != _identifier(status_block_id):
            raise CloudRegistrationError('기존 상태 블록과 전달된 상태 블록이 다릅니다.')
        root = state['objects']['root']['id']
        meal = state['extras']['meal_block_id']
        owned_meal = state['objects'].get('extras:meals', {}).get('id')
        if owned_meal and _identifier(owned_meal) != _identifier(meal):
            raise CloudRegistrationError('저장된 급식 블록 식별자가 서로 다릅니다.')
        if state['extras'].get('root_id') and _identifier(state['extras']['root_id']) != _identifier(root):
            raise CloudRegistrationError('급식 표시가 설치 홈과 다른 페이지에 속합니다.')
        manifest = validate_manifest({
            'version': 1, **{key: school.get(key) for key in SCHOOL_KEYS if key != 'academic_year'},
            'academic_year': year, 'root_page_id': root,
            'agenda_data_source_id': state['databases']['agenda']['data_source_id'],
            'meals_block_id': meal, 'status_block_id': status_block_id or recorded_status,
        })
        binding = hashlib.sha256(json.dumps({key: manifest[key] for key in
            ('office_code', 'school_code', 'academic_year')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if any(state.get(key) and state[key] != binding for key in ('neis_source', 'neis_meals_source')):
            raise CloudRegistrationError('기존 NEIS 연결과 학교·교육청·학년도가 다릅니다.')
        return manifest
    except (KeyError, TypeError, AttributeError):
        raise CloudRegistrationError('저장된 설치·급식·상태 블록과 학교 설정을 확인하세요.') from None


def _private_path(value):
    try:
        path = Path(value).resolve()
        if path.suffix.lower() != '.json' or not any(part in ('.local', 'private') for part in path.parts[2:-1]):
            raise ValueError
        return path
    except (ValueError, TypeError, OSError, RuntimeError):
        raise CloudRegistrationError('연결 정보 파일은 .local/ 또는 private/ 아래 JSON을 사용하세요.') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudRegistrationError('JSON에 중복된 필드가 있습니다.')
        result[key] = value
    return result


def _load_private_json(path, maximum):
    target = _private_path(path)
    try:
        with target.open('rb') as file:
            data = file.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise CloudRegistrationError('비공개 JSON 파일을 읽거나 검증할 수 없습니다.') from None


def add_commands(commands):
    command = commands.add_parser('cloud-connect', help='기존 설치 정보로 클라우드 연결 링크 준비; 접속·연결 실행 없음')
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', help='MCP 또는 REST 담당자가 만든 비공개 manifest v1 JSON')
    source.add_argument('--state', help='완료된 REST 설치 상태; MCP 상태에는 사용하지 않음')
    command.add_argument('--school-config', help='설치 담당자가 가진 학교 설정 또는 NEIS 조회 JSON')
    command.add_argument('--status-block-id', help='이미 만든 동기화 상태 표시 블록 UUID')
    command.add_argument('--service-url', required=True, help='실제 서비스의 HTTPS origin; 예시 주소로 연결 완료를 주장하지 않음')
    command.add_argument('--output', help='정규화한 manifest를 .local/ 또는 private/ 아래 JSON으로 저장')


def run(args):
    if args.manifest and (args.school_config or args.status_block_id):
        raise CloudRegistrationError('--manifest에는 REST 학교·상태 블록 옵션을 함께 사용하지 않습니다.')
    # Validate origin first; never load unrelated inputs for an invalid URL.
    origin = _service_origin(args.service_url)
    if args.manifest:
        manifest = validate_manifest(_load_private_json(args.manifest, MAX_MANIFEST_BYTES))
    else:
        state = _load_private_json(args.state, MAX_STATE_BYTES)
        school = _load_private_json(args.school_config, MAX_STATE_BYTES) if args.school_config else None
        manifest = build_manifest(state, school, status_block_id=args.status_block_id)
    url = enrollment_url(origin, manifest)
    if args.output:
        from .cli import private_json
        output = _private_path(args.output)
        if output in {_private_path(path) for path in (args.manifest, args.state, args.school_config) if path}:
            raise CloudRegistrationError('출력은 기존 설치·학교·연결 정보와 다른 파일에 저장하세요.')
        try:
            private_json(output, manifest)
        except (OSError, ValueError):
            raise CloudRegistrationError('정규화한 연결 정보를 비공개 JSON에 저장할 수 없습니다.') from None
    print(json.dumps({'enrollment_url': url, 'connected': False, 'daily_sync_enabled': False,
                      'requested_schedule': 'daily', 'manifest_saved': bool(args.output),
                      'next_step': '연결 링크에서 Notion 승인을 완료하고 서비스의 연결 결과를 확인하세요. 아직 자동 갱신은 켜지지 않았습니다.'},
                     ensure_ascii=False, indent=2))
    return 0
