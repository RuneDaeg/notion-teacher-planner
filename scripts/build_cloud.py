"""Stage an allowlisted Firebase Functions source tree without local secrets."""
import argparse
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


PACKAGES = ('cloud', 'teacher_planner')
PARAMETERS = {'NOTION_CLIENT_ID', 'PUBLIC_BASE_URL'}
PROJECT_ID = re.compile(r'[a-z][a-z0-9-]{4,28}[a-z0-9]\Z')
MODULE = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\.py\Z')


class BundleError(ValueError):
    """A deliberately redacted staging error."""


def _plain(path, *, directory=False):
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise ValueError
    except (OSError, ValueError):
        raise BundleError('배포 소스와 대상은 심볼릭 링크가 아닌 일반 파일·디렉터리여야 합니다.') from None


def _bytes(path):
    _plain(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError
            return file.read()
    except (OSError, ValueError):
        raise BundleError('허용된 배포 소스를 읽을 수 없습니다.') from None


def _parameter_file(path):
    """Firebase may save only its two public deployment parameters here."""
    try:
        content = _bytes(path)
        if len(content) > 4096:
            raise ValueError
        values = {}
        for line in content.decode('utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not separator or key not in PARAMETERS or key in values:
                raise ValueError
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError
                value = value[1:-1]
            if not value or any(char in value for char in '\\$\r\n\x00'):
                raise ValueError
            values[key] = value
        if 'NOTION_CLIENT_ID' in values:
            value = values['NOTION_CLIENT_ID']
            if str(UUID(value)) != value.lower():
                raise ValueError
        if 'PUBLIC_BASE_URL' in values:
            parts = urlsplit(values['PUBLIC_BASE_URL'])
            if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                    or parts.password is not None or parts.path not in ('', '/')
                    or parts.query or parts.fragment or parts.port not in (None, 443)
                    or any(char.isspace() for char in values['PUBLIC_BASE_URL'])):
                raise ValueError
    except (UnicodeError, ValueError):
        raise BundleError('배포 매개변수 파일에는 유효한 NOTION_CLIENT_ID·PUBLIC_BASE_URL만 허용합니다.') from None


def _sources(root):
    paths = [Path('main.py'), Path('requirements.txt')]
    for package in PACKAGES:
        directory = root / package
        _plain(directory, directory=True)
        modules = sorted(directory.glob('*.py'))
        if not any(path.name == '__init__.py' for path in modules):
            raise BundleError('배포에 필요한 Python 패키지가 없습니다.')
        for path in modules:
            if not MODULE.fullmatch(path.name):
                raise BundleError('허용되지 않은 Python 모듈 파일 이름입니다.')
            paths.append(Path(package) / path.name)
    paths.extend(Path('teacher_planner') / name for name in ('blueprint.json', 'dashboard.json'))
    return {path: _bytes(root / path) for path in paths}


def _target_tree(root, target, sources):
    # Check each staging ancestor before walking or creating anything under it.
    for path in (root / '.local', root / '.local/firebase', target):
        if path.exists() or path.is_symlink():
            _plain(path, directory=True)
    if not target.exists():
        return []
    generated = []
    allowed_dirs = {Path('.'), *(Path(package) for package in PACKAGES)}
    for relative in allowed_dirs:
        directory = target / relative
        if not directory.exists() and not directory.is_symlink():
            continue
        _plain(directory, directory=True)
        for child in directory.iterdir():
            entry = relative / child.name
            if entry in sources:
                _plain(child)
            elif relative == Path('.') and child.name in PACKAGES:
                _plain(child, directory=True)
            elif relative == Path('.') and child.name == 'venv':
                # Virtual environments contain expected interpreter symlinks.
                # Their contents are neither read, copied nor changed here.
                _plain(child, directory=True)
            elif relative == Path('.') and child.name.startswith('.env.') and PROJECT_ID.fullmatch(child.name[5:]):
                _parameter_file(child)
            elif child.name == '__pycache__':
                _plain(child, directory=True)
                stems = {path.stem for path in sources if path.parent == relative and path.suffix == '.py'}
                for cache in child.iterdir():
                    _plain(cache)
                    match = re.fullmatch(r'([A-Za-z_][A-Za-z_0-9]*)\.cpython-[0-9]{2,3}(?:\.opt-[012])?\.pyc', cache.name)
                    if not match or match[1] not in stems:
                        raise BundleError('배포 대상에 알 수 없는 캐시 파일이 있습니다.')
                    generated.append(cache)
                generated.append(child)
            else:
                raise BundleError('배포 대상에 허용되지 않은 파일이 있습니다. 비밀 환경 파일을 배포할 수 없습니다.')
    return generated


def _mkdir(root, directory):
    current = root
    for component in directory.relative_to(root).parts:
        current = current / component
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        _plain(current, directory=True)


def build(repo_root):
    """Return the number of staged source files; never load the root environment."""
    root = Path(repo_root).absolute()
    _plain(root, directory=True)
    root = root.resolve()
    target = root / '.local/firebase/functions'
    sources = _sources(root)
    generated = _target_tree(root, target, sources)
    # Everything is validated before changing an existing deployment bundle.
    for path in generated:
        if path.name == '__pycache__':
            path.rmdir()
        else:
            path.unlink()
    for relative, data in sources.items():
        destination = target / relative
        _mkdir(root, destination.parent)
        descriptor, temporary = tempfile.mkstemp(prefix='.bundle-', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'wb') as file:
                file.write(data)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return len(sources)


def main(argv=None):
    parser = argparse.ArgumentParser(description='허용된 소스만 Firebase 배포 폴더에 준비합니다.')
    parser.parse_args(argv)
    try:
        count = build(Path(__file__).resolve().parents[1])
    except (BundleError, OSError):
        print('배포 준비 실패: 허용된 소스·대상·공개 매개변수 파일을 확인하세요.')
        return 1
    print(f'배포 소스 {count}개 준비 완료.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
