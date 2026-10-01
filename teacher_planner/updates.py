"""Prepare an update link from exact recorded IDs; never change install state."""
import argparse
import base64
import json
import re
from pathlib import Path

from .cloud_registration import CloudRegistrationError, _identifier, _service_origin, validate_manifest


def validate_targets(value):
    keys = {'version', 'students_data_source_id', 'counseling_data_source_id', 'student_relation_property_id'}
    if not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1:
        raise CloudRegistrationError('업데이트 대상은 정해진 네 필드만 사용하세요.')
    students = _identifier(value['students_data_source_id'])
    counseling = _identifier(value['counseling_data_source_id'])
    prop = value['student_relation_property_id']
    if (students == counseling or not isinstance(prop, str) or not re.fullmatch(r'[\x21-\x7e]{1,128}', prop)
            or prop in {'__proto__', 'constructor', 'prototype'}):
        raise CloudRegistrationError('서로 다른 원본 데이터 소스와 반환된 관계 속성 ID를 확인하세요.')
    return {'version': 1, 'students_data_source_id': students, 'counseling_data_source_id': counseling,
            'student_relation_property_id': prop}


def update_url(service_url, manifest, targets):
    origin = _service_origin(service_url)
    # Same strict public HTTPS origin contract as the Cloudflare worker.
    if ':' in origin.removeprefix('https://'):
        raise CloudRegistrationError('업데이트 서비스 주소에는 포트를 넣지 마세요.')
    bundle = {'connection': validate_manifest(manifest), 'updates': validate_targets(targets)}
    if {bundle['updates'][key] for key in ('students_data_source_id', 'counseling_data_source_id')} & {
        bundle['connection'][key] for key in ('root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id')
    }:
        raise CloudRegistrationError('학생·상담 원본과 기존 갱신 대상을 구분하세요.')
    encoded = base64.urlsafe_b64encode(json.dumps(bundle, ensure_ascii=False, separators=(',', ':')).encode()).decode().rstrip('=')
    return origin + '/connect#' + encoded


def main():
    parser = argparse.ArgumentParser(description='기존 교무수첩의 선택 업데이트 링크 준비 (Notion 쓰기 없음)')
    parser.add_argument('--service-url', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--targets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = [args.manifest, args.targets]
        if any(path.stat().st_size > 8192 for path in paths):
            raise CloudRegistrationError('설정 파일 크기를 확인하세요.')
        if args.output.resolve() in {path.resolve() for path in paths}:
            raise CloudRegistrationError('출력은 입력 파일과 다른 경로여야 합니다.')
        url = update_url(args.service_url, *(json.loads(path.read_text()) for path in paths))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'update_url': url}, ensure_ascii=False, indent=2) + '\n')
        print('업데이트 링크를 지정한 출력 파일에 저장했습니다. 기존 설치 상태는 변경하지 않았습니다.')
    except (CloudRegistrationError, OSError, ValueError):
        parser.exit(2, '업데이트 링크를 만들지 못했습니다. 파일 경로와 정해진 대상 필드를 확인하세요.\n')


if __name__ == '__main__':
    main()
