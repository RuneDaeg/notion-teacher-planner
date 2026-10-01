# Cloudflare 무료 플랜으로 시범 운영하기

이 경로는 **Cloudflare Workers + D1 + Cron Triggers**로 교무수첩의 급식·학사일정을 매일 갱신한다. Notion 페이지·데이터베이스와 교사의 사용 방식은 유지한다. 운영자는 서버를 한 번 준비하고, 교사는 설치 AI가 만든 링크에서 기존 Notion 수첩의 접근을 승인한다. 학교를 다시 입력하거나 각자 NEIS 키를 발급받지 않는다. 공통 연결 절차와 manifest는 [CLOUD_SYNC.md](CLOUD_SYNC.md)를 따른다.

**아직 실제 계정 설정·배포·OAuth 등록이 끝나지 않았다면 자동 갱신은 꺼진 상태다.** 명령 예시나 로컬 테스트를 실제 배포로 보고하지 않는다. 운영자의 Cloudflare 계정과 실제 서비스 주소를 확인하기 전에는 교사용 연결 링크에 임의의 호스트를 넣지 않는다.

## 무료로 시작할 수 있는 범위

Cloudflare Workers의 공식 안내는 카드 없이 무료 시작을 제공한다. Workers Free와 D1의 실제 제한을 확인하고 작은 규모에서 CPU·DB 사용량을 측정한다. [Workers 공식 안내](https://www.cloudflare.com/products/workers/)

| 항목 | 공식 무료 플랜 한도 |
| --- | --- |
| Workers 요청 | 계정 전체 하루 100,000회 |
| Workers CPU | HTTP·Cron 호출당 10ms. 외부 응답을 기다리는 시간은 CPU 시간에서 제외 |
| 외부 하위 요청 | 호출당 50회 |
| Cron | 계정당 5개 |
| D1 행 읽기·쓰기 | 하루 읽기 5,000,000행 / 쓰기 100,000행 |
| D1 저장소 | 계정 전체 5GB |

무료 요청·D1 일일 한도는 UTC 날짜로 집계하며 서비스의 서울 날짜와 다르다. D1 일일 한도를 넘으면 쿼리가 실패하고, CPU를 지속적으로 초과하면 Worker 실행이 중단될 수 있다. 실제 계정의 다른 앱 사용량도 함께 계산해야 한다. [Workers 제한](https://developers.cloudflare.com/workers/platform/limits/), [D1 요금과 초과 동작](https://developers.cloudflare.com/d1/platform/pricing/)

이 구현은 Queues나 Durable Objects를 사용하지 않는다. 현재 Firebase Python 코드를 Cloudflare에 그대로 올리는 것이 아니라 별도의 JavaScript Worker와 D1 저장소를 사용한다. 모든 규모에서 무료라는 보장은 없으며, 무료 한도를 넘는 문제를 자동 유료 전환으로 해결하지 않는다.

## 매분 실행과 하루 한 번 갱신의 관계

Cron은 매분 작업을 확인하지만, **수첩 한 개의 일일 갱신은 한국 시간 오전 7시 이후 하루 한 번 완료하는 것이 목표**다. 한 번에 처리할 작업량을 제한하고 D1에 진행 위치·잠금·다음 실행 시각을 저장한다. 다음 Cron 실행은 이어서 남은 단계를 처리한다. 최초 등록이나 일시 중지 후 재개는 오전 7시를 기다리지 않고 실행 대상이 된다. 실제 완료 시각은 남은 작업·재시도·한도에 따라 늦어질 수 있다.

학교별 NEIS 조회 결과는 재사용하고 교사별 Notion 변경만 개별 처리한다. 날짜별 행사 원본을 연속 기간으로 묶는 규칙과 기존 외부 ID를 유지한다. API 오류로 마지막 정상 급식을 비우거나, 행사 누락을 삭제·취소로 해석하지 않는다. 수동 메모·관계를 보존하고 모호한 중복·기간 변화는 확인 대상으로 멈춘다.

Cron 설정은 UTC 기준이므로 실행 여부와 일일 중복 방지는 코드에서 `Asia/Seoul` 날짜·시간으로 판단한다. 매분 Cron을 매일 오전 7시 한 번 실행으로 바꾸면 분할 작업을 계속할 실행 기회가 없어지므로 현재 구조의 대체 설정으로 사용하지 않는다. [Cloudflare Cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/)

### 이 저장소의 시범 운영 상한

- `MAX_INSTALLATIONS`: 기본 등록 상한 **수첩 50개**.
- `MAX_DAILY_STEPS`: 기본 처리 상한 **서울 날짜 기준 하루 작업 1,000단계**. 한 단계는 수첩 한 개의 전체 완료나 Notion API 요청 한 번과 같지 않다.
- 한도에 도달하면 추가 등록·처리를 중단한다. 기존 기록을 삭제하거나 성공으로 처리하지 않는다.

처음 연결한 수첩에 기간으로 묶은 행사 80개를 새로 넣는다면 급식 1단계·행사 80단계·완료 확인 1단계로 최소 82단계가 필요하다. 다른 대기 수첩이 없어도 매분 한 단계 구조에서 약 82분이 기본 처리 시간이며, 대기·재시도는 더해진다. 같은 규모의 새 수첩 50개는 최소 4,100단계이므로 기본 일일 상한으로는 최소 5일에 걸쳐 초기 학사일정을 채운다. 이는 원본이 안정적이고 오류가 없다는 가정의 하한이며, 기존 수첩의 일일 갱신도 같은 예산을 사용한다. 등록 상한 50개를 하루에 50개 초기 설정을 완료한다는 뜻으로 설명하거나 오전 7시 완료를 약속하지 않는다.

오늘 급식은 해당 수첩에 처음 배정된 단계에서 먼저 표시하므로 전체 학사일정 처리가 끝날 때까지 기다리지 않는다. 학사일정 원본에 변화가 없는 평소 갱신은 급식과 완료 확인을 한 단계에 처리한다. 초기 행사 반영이 진행 중일 때는 급식이 오늘 것으로 바뀌었어도 전체 완료 상태와 마지막 성공 시각은 아직 갱신되지 않을 수 있다.

이 상한은 앱 내부 보호 장치다. 공개 OAuth 접속·Cron 호출·DB 조회 등 모든 플랫폼 사용량을 집계하는 결제 차단 장치는 아니다. 설정을 높이거나 유료 플랜을 선택하더라도 요금의 절대 상한이 되지 않는다. 수천·수만 교사의 처리 시간을 검증했다고 안내하지 않는다.

## 운영자 준비

1. Cloudflare 계정과 Workers Free 사용 가능 상태를 확인한다. 교사마다 Cloudflare 계정을 만들 필요는 없다.
2. 서비스용 D1 데이터베이스를 만들고 반환된 실제 DB ID를 로컬 배포 설정에 기록한다. 계정·DB ID가 들어간 개인 설정은 공개 Git에 올리지 않는다.
3. Notion 공개 OAuth 연결을 준비하고 콘텐츠 읽기·수정·삽입 세 권한만 켠다. 댓글 읽기·삽입은 끄고 사용자 정보는 **사용자 정보 없음(No user information)**으로 설정한다. 템플릿 복제를 등록하지 않고 기존 수첩을 선택하도록 한다. Redirect URI는 실제 서비스 기본 URL의 `/api/callback`이다. [Notion 공개 연결](https://developers.notion.com/guides/get-started/public-connections), [권한 설정](https://developers.notion.com/reference/capabilities)
4. 아래 비밀값과 공개 매개변수를 설정한다. 운영자가 보유한 NEIS 키 한 개로 학교별 공개 자료를 조회한다.
5. 실제 Worker 배포와 D1 스키마 적용 후 테스트 수첩으로 OAuth·등록·일일 갱신·중복 실행·일시 중지를 확인한다.

승인자 구분에는 OAuth 응답의 `owner.user.id`를 사용하며 이름·프로필 사진·이메일은 필요하지 않다. 공식 문서는 OAuth `owner`에 승인한 사용자의 객체가 반환되고 User 객체의 `object`와 `id`는 항상 포함된다고 명시한다. 사용자 정보 권한을 꺼도 이 식별값을 사용하는 설계이며, 실제 최소 권한 승인에서 등록 성공을 확인한다. 기존 연결의 권한을 변경했다면 다시 OAuth 승인을 받는다. [OAuth 응답](https://developers.notion.com/guides/get-started/authorization), [User 객체](https://developers.notion.com/reference/user), [권한 변경](https://developers.notion.com/reference/capabilities)

| 설정 위치 | 이름 | 용도 |
| --- | --- | --- |
| Workers secret | `NEIS_API_KEY` | 공개 급식·학사일정 조회 |
| Workers secret | `NOTION_CLIENT_SECRET` | Notion OAuth 코드 교환 |
| Workers secret | `TOKEN_ENCRYPTION_KEY` | AES-GCM용 무작위 32바이트를 base64url로 인코딩한 키 |
| Worker 변수 | `NOTION_CLIENT_ID` | 공개 OAuth 클라이언트 ID |
| Worker 변수 | `PUBLIC_BASE_URL` | 실제 서비스의 HTTPS origin. 경로·끝 슬래시 제외 |

비밀값은 Wrangler의 대화형 secret 입력으로 등록한다. 공개 설정 파일이나 명령 인수에 직접 쓰지 않는다. 로컬 Python용 루트 `.env`를 Worker의 공개 변수에 복사하지 않는다. [Workers secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

`TOKEN_ENCRYPTION_KEY`라는 이름은 Firebase 경로와 같지만 **암호화 형식은 다르다**. Cloudflare는 AES-GCM을 사용하고 Firebase는 Fernet을 사용하므로 기존 암호문·토큰 저장 레코드를 그대로 복사하지 않는다. 이미 등록한 서버의 키만 바꾸면 토큰을 읽지 못하므로 키 교체·재연결 계획이 먼저 필요하다.

## 배포 절차

다음은 운영자가 수행하는 절차다. **계정의 Workers 플랜이 Free인지 먼저 확인**하고 진행한다. 이 저장소의 명령이 기존 계정 요금제를 변경하거나 결제를 취소하지는 않는다. Node.js 22.13.0 이상과 npm이 필요하며 Wrangler 버전은 패키지에 고정되어 있다.

저장소 루트에서 시작한다.

```bash
cd cloudflare
npm ci
npm test
npm run test:runtime
npm run check
npm run dry-run
```

`npm test`는 외부 API를 모의 처리한 로직 검증이다. 별도 `npm run test:runtime`은 Wrangler에 포함된 Miniflare로 실제 `workerd`를 실행해 요청 옵션·타임아웃·리다이렉트 거부 호환성을 확인한다. 이 검사도 외부 응답은 모의 처리하며 실제 토큰이나 NEIS·Notion 네트워크를 사용하지 않는다. 로컬 루프백 수신이 가능한 환경에서 실행하고, CI에서도 일반 테스트 다음에 실행한다.

`npm run check`는 문법을 검사한다. `npm run dry-run`은 예시 설정으로 배포 파일을 검증해 `.local/cloudflare-build`에 준비한다. 이 로컬 검사들은 실제 배포·OAuth 승인·NEIS 조회·Notion 반영·무료 CPU 한도 내 운영을 증명하지 않는다. 실제 서비스 검증은 아래 ‘기존 수첩을 연결하고 확인하기’ 절차로 별도 수행한다.

운영자 계정을 연결하고 서비스용 D1을 만든다.

```bash
npx wrangler login
npx wrangler whoami
npx wrangler d1 create teacher-planner
cp wrangler.example.jsonc wrangler.jsonc
```

`cloudflare/wrangler.jsonc`는 Git에서 제외하는 개인 배포 설정이다. 파일 확장자는 `.jsonc`지만 배포 검사기는 일반 JSON을 읽으므로 주석·끝 쉼표를 넣지 않는다. 다음 값을 실제로 확인해 바꾼다.

| 항목 | 입력할 값 |
| --- | --- |
| `name` | 사용할 실제 Worker 이름 |
| `d1_databases[0].database_id` | D1 생성 결과의 실제 ID |
| `d1_databases[0].database_name` | 생성한 DB 이름. 명령 예시는 `teacher-planner` |
| `vars.NOTION_CLIENT_ID` | 운영자의 Notion 공개 OAuth 클라이언트 ID |
| `vars.PUBLIC_BASE_URL` | Workers 대시보드에서 확인한 실제 `workers.dev` HTTPS origin 또는 설정한 사용자 도메인 |
| `vars.MAX_INSTALLATIONS` | 초기에는 `"50"` 유지 |
| `vars.MAX_DAILY_STEPS` | 초기에는 `"1000"` 유지 |

DB 바인딩 `DB`, 스키마 경로 `migrations`, 공유 연결 UI 경로 `../cloud/web`, 매분 Cron `* * * * *`는 예시와 일치시킨다. `PUBLIC_BASE_URL`에 예시 도메인을 넣어 연결된 것처럼 안내하지 않는다. 계정의 실제 하위 도메인이 확인되지 않았다면 먼저 Workers 대시보드에서 설정·확인한다. Notion 연결 설정의 Redirect URI도 같은 origin 뒤의 `/api/callback`로 등록한다.

스키마를 적용하고 세 비밀값을 입력한 뒤 배포한다. 아래 작업은 앞에서 이동한 `cloudflare/` 안에서 실행한다. DB 이름을 바꿨으면 명령도 실제 이름으로 바꾼다.

```bash
npx wrangler d1 migrations apply teacher-planner --remote --config wrangler.jsonc
npx wrangler secret put NEIS_API_KEY --config wrangler.jsonc
npx wrangler secret put NOTION_CLIENT_SECRET --config wrangler.jsonc
npx wrangler secret put TOKEN_ENCRYPTION_KEY --config wrangler.jsonc
npm run deploy
```

`secret put`의 입력창에 실제 값을 제공하고 채팅·Git에 붙여 넣지 않는다. 암호화 키는 무작위 32바이트의 base64url 값으로 별도 생성해 비밀 관리 도구에 보관한다. Firebase Fernet의 기존 암호문을 이 키로 복호화할 수 있다고 가정하지 않는다.

`npm run deploy`는 개인 설정의 예시 DB ID·예시 origin·미설정 클라이언트 ID, `vars` 안의 비밀값을 검사해 배포를 중단한다. 이를 통과해도 계정 요금제나 실제 OAuth 성공이 확인되는 것은 아니다. 배포 후 표시된 실제 URL과 설정한 `PUBLIC_BASE_URL`이 같은지 확인한다.

등록 주소는 실제 서비스의 `/connect`, OAuth 콜백은 `/api/callback`이다. `/api/start`, `/api/status`, `/api/enabled`는 연결 화면이 사용하는 서버 경로다. D1 바인딩과 공유 정적 화면이 배포에 포함되었는지 확인한 뒤에만 교사에게 실제 링크를 제공한다.

### 기존 배포에 선택 업데이트 기능 추가

기존 운영자는 D1이나 비밀값을 새로 만들지 않는다. 원래 개인 배포 설정과 암호화 키를 유지한 채 `npx wrangler d1 migrations apply teacher-planner --remote --config wrangler.jsonc`로 `0002_template_updates.sql`을 적용한 뒤 새 Worker와 연결 화면을 함께 배포한다. DB 이름은 기존 설정을 따른다. 이 마이그레이션은 수첩별 선택 업데이트 이력 테이블을 추가하며 기존 연결 manifest와 일일 갱신 상태를 바꾸지 않는다.

관리 화면은 `GET /api/updates`, `POST /api/updates/apply`, `POST /api/updates/confirm-layout`을 사용한다. 조회는 로그인한 수첩으로 제한하고 변경은 같은 출처·세션 소유자·CSRF를 검증한다. Notion 변경은 일일 갱신과 같은 수첩 잠금을 사용한다. 템플릿 업데이트가 일일 갱신을 완료 처리하거나 다음 실행 시각을 변경하지 않으며, 일일 갱신이 일시 중지되어도 유효한 연결로 선택 업데이트를 진행할 수 있다.

첫 항목 `student-history-v1`은 기존 양방향 관계를 검증하거나 상담 DB가 빈 경우에만 관계를 전환한다. 기록이 있는 단방향 관계는 `assistance_required`로 보류한다. 공통 학생 레이아웃·정렬은 서버 API가 설정하지 않으며 `schema_applied` 후 실제 화면 확인이 필요하다. `complete`는 관계 재검사와 교사의 화면 확인 기록을 뜻한다. 서버의 화면 검증 성공으로 해석하지 않는다.

배포 후 [기존 수첩 업데이트](UPDATES.md)의 링크 생성·대상 등록·재시도·화면 확인 과정을 테스트 수첩으로 검증한다. 저장되는 업데이트 정보는 정확한 DB·속성 ID와 제한된 작업 기록이며 학생·상담 본문은 저장하지 않는다. 기존 설치 AI가 홈에 업데이트 링크를 한 번 추가해야 한다. 서버 배포만으로 모든 교사의 페이지에 링크나 새 화면을 일괄 추가하지 않는다.

## 기존 수첩을 연결하고 확인하기

`cloud-connect` 명령은 서버 종류에 관계없이 실제 HTTPS 서비스 주소를 받는다. [공통 연결 안내](CLOUD_SYNC.md#설치-ai가-연결-링크-만들기)의 manifest와 CLI를 사용한다. 배포·링크 생성이 기존 MCP 상태를 Python 상태로 바꾸거나 Notion 블록을 자동 재구성하지 않는다.

첫 연결 전 급식·상태 콜아웃과 원본 data source를 실제로 확인한다. 자식 없는 전용 콜아웃, 또는 자체 본문은 비어 있고 활성 문단 자식이 정확히 하나인 전용 콜아웃을 지원한다. 후자는 문단에 자식이 없고 `parent.block_id`가 해당 콜아웃과 일치하며, 다른 자식이나 조회의 다음 페이지가 없어야 한다. 이 조건에 맞는 기존 MCP 콜아웃은 그대로 사용한다. manifest에는 문단이 아닌 **바깥 콜아웃 ID**를 기록하며, 서버는 매번 검증한 문단 본문만 갱신한다.

지원 형태 밖의 자식·수동 메모가 있는 기존 급식 영역은 보존하고, 필요하면 요청받은 전환 범위에서 새 전용 콜아웃을 준비한다. 클라우드에 학생·상담·연락 원문을 등록하지 않는다. Notion 상위 페이지 승인 시 하위 페이지 접근도 포함될 수 있으므로 ‘서버가 해당 내용을 수집하지 않음’과 ‘접근 권한이 없음’을 구분한다.

운영 확인은 다음 순서로 진행한다.

1. 연결 링크에서 기존 학교·학년도가 표시되고 학교 재입력을 요구하지 않는지 확인한다.
2. Notion OAuth에서 기존 수첩을 선택하고 대상·스키마 검증이 통과하는지 확인한다.
3. 예약 실행이 제한된 단계를 처리한 뒤 오늘 식단·기간 행사를 반영하고 마지막 성공 시각을 기록하는지 확인한다.
4. 같은 작업을 다시 실행해 중복 페이지가 생기지 않는지 확인한다. 실패를 빈 식단이나 성공으로 표시하지 않는지도 확인한다.
5. 일시 중지·재개 및 잘못된 권한·일일 처리 상한에서의 중단을 확인한다. 실제 CPU와 D1 사용량을 기록한다.

로컬 테스트만 통과했으면 위 운영 검증은 미완료로 보고한다. 새 서버를 쓰기 전 같은 수첩의 로컬 `--watch`나 기존 Firebase 갱신은 멈춰 중복 반영을 피한다.

## Firebase에서 전환할 때

Cloudflare 배포는 Firebase를 자동 삭제·중지하거나 Blaze 결제를 해제하지 않는다. 기존 Firebase 사용자가 있다면 해당 수첩의 갱신을 먼저 중지한 뒤 Cloudflare에서 새 OAuth 승인을 받는다. 운영자의 NEIS 키는 서비스 비밀로 설정할 수 있지만 교사별 Notion 토큰·Firebase 암호문·MCP 토큰은 전송하지 않는다.

Firebase 프로젝트의 별도 저장소·예약·결제 정리는 실제 사용 중인 자원을 확인한 뒤 운영자가 결정한다. 기존 Firebase 경로의 설정은 [FIREBASE.md](FIREBASE.md)에 보존되어 있다.
