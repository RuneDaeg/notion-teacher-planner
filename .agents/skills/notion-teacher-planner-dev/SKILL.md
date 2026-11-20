---
name: notion-teacher-planner-dev
description: 노션 교무수첩 프로젝트의 웹 설문, 기능 예시, 배치 미리보기, 수정 요청문 화면을 개발자가 검토하거나 고칠 때 사용한다. UI 재현, 브라우저 회귀 테스트, 요청받은 Cloudflare 배포와 공개 화면 검증을 지원한다.
---

# 교무수첩 웹 개발

작업 대상은 [교무수첩 저장소](https://github.com/RuneDaeg/notion-teacher-planner)의 웹 소스다. 스킬만 설치했다면 기존 작업용 저장소를 찾거나 지정된 버전을 작업 폴더에 복제한다. 경로는 해당 저장소 루트 기준이다. 프로젝트의 `AGENTS.md`와 `START_HERE.md`를 확인한다. 검토 요청에는 재현한 문제·사용자 영향·근거를 전달하고, 수정이나 배포도 요청받았으면 그 단계까지 진행한다.

## 화면 검토와 수정

교사가 실제로 어떤 화면에서 출발해 무엇을 누르고 어떤 결과를 기대하는지 먼저 재현한다. UI 검토는 버튼 명칭·순서, 드롭 위치 표시, 키보드·포커스, 화면 폭에 따른 가독성을 해당 동작에서 살핀다. 수정할 때는 다음 진입점에서 화면과 상태 처리를 함께 추적한다.

| 사용자 동작 | 확인할 코드 |
| --- | --- |
| 설문에서 옵션을 고르고 미리보기로 이동 | `cloud/web/setup.js`, `cloud/web/preview.js`, `cloud/web/handoff.mjs` |
| 구역·열을 옮기고 배치를 내려받기 | `cloud/web/preview-model.mjs`, `cloud/web/preview.js`, `docs/PREVIEW.md` |
| 기능 예시에서 프리셋을 고르고 수정 요청문 만들기 | `cloud/web/features.js`, `cloud/web/workflow-picker.mjs`, `cloud/web/edit.js`, `cloud/web/edit-model.mjs` |

화면을 오가는 변경은 새 탭에서 시작해 같은 탭으로 테스트한다. 설문이나 프리셋에서 넘겨준 선택은 한 번 반영하고, 이후 새로고침에서는 사용자가 마지막으로 바꾼 열 폭·순서·입력값을 확인한다. 배치를 내보내는 변경은 다운로드한 `reviewed-layout.json`의 내용도 대조한다. 레이아웃과 기능 목록의 원본을 바꾸면 저장소 루트에서 `python scripts/build_preview_catalog.py`로 웹 카탈로그를 재생성하고 `--check`로 일치 여부를 확인한다.

## 브라우저 검증

검증 명령은 `cloudflare/`에서 실행한다. Node.js 버전은 `cloudflare/package.json`의 engines를 따른다. 새 작업 환경은 `npm ci`와 `npx playwright install chromium --only-shell`로 준비한다. Linux CI에서 필요한 시스템 의존성은 `.github/workflows/test.yml`을 따른다. `npm test`는 Python 설치기와 웹의 결과도 대조한다. 기본 `python3`이 3.10 미만이면 `PYTHON` 환경 변수로 지원되는 인터프리터를 지정한다.

- `npm run test:browser`는 로컬 Worker를 띄워 Chromium에서 실제 화면을 검사한다. 시나리오는 `cloudflare/browser-test/planner.spec.mjs`에 있다. 예를 들어 키보드 조작만 확인하려면 `npm run test:browser -- -g "keyboard focus"`를 실행한다.
- 수정 요청이면 보고된 순서에서 실패하는 동작을 확인하고, 기존 테스트로 잡히지 않는 회귀에는 같은 순서의 테스트를 추가한다. 테스트에는 가상 자료를 쓴다. 초안 보존은 새로고침 전후의 실제 값으로, 키보드 복귀는 편집 후 실제로 포커스를 받은 버튼으로 판정한다.
- 모델 로직에는 `npm test`, 문법에는 `npm run check`, Worker 런타임 변경에는 `npm run test:runtime`을 사용한다. Python 설치기·배치 계약까지 바뀌면 루트에서 `python -m unittest discover -s tests -v`와 `python scripts/build_preview_catalog.py --check`도 실행한다.

## 배포 요청을 받았을 때

기존 서비스의 운영 절차는 `docs/CLOUDFLARE.md`를 따른다. `cloudflare/`에서 `npm run dry-run`으로 패키징을 확인하고, 대상 Worker와 운영 설정(`wrangler.jsonc`)을 확인한 뒤 `npm run deploy`를 실행한다. 운영 인증이 만료되면 계정 소유자의 승인을 받아 이어 간다. 일반 웹 화면 배포에는 기존 D1·시크릿 설정을 재사용한다.

배포 출력의 HTTPS 주소를 `PLANNER_BROWSER_BASE_URL`에 설정하여 같은 `npm run test:browser`를 다시 실행한다. 공개 화면 검증은 로그인 없는 브라우저 시나리오와 가상 입력을 사용한다. 결과에 실제 확인한 URL·동작·실패 항목을 기록하고, 요청에 GitHub 반영이 포함됐다면 해당 커밋의 CI 결과도 확인한다.
