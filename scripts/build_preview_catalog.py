#!/usr/bin/env python3
"""Publish the exact repository layout/view catalog used by browser previews."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from teacher_planner.preview_bundle import build_catalog  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    catalog = build_catalog()
    targets = {
        ROOT / 'cloud/web/preview-catalog.json': json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
        ROOT / 'cloud/web/workflow-catalog.json': json.dumps(catalog['workflows'], ensure_ascii=False, indent=2) + '\n',
    }
    if args.check:
        if any(not target.is_file() or target.read_text(encoding='utf-8') != content for target, content in targets.items()):
            print('미리보기 카탈로그를 다시 생성하세요: python scripts/build_preview_catalog.py', file=sys.stderr)
            return 1
        print('미리보기 카탈로그가 공통 설계와 일치합니다.')
        return 0
    for target, content in targets.items():
        target.write_text(content, encoding='utf-8')
    print('cloud/web/preview-catalog.json · workflow-catalog.json 생성 완료')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
