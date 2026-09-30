# Firebase로 매일 급식·학사일정 갱신하기

교무수첩은 계속 **사용자의 Notion 페이지와 원본 데이터베이스**를 사용한다. 선택형 Firebase 서비스는 NEIS 공개 자료를 정해진 시간에 가져와 기존 급식 블록과 업무·일정 원본에 반영한다. 교사가 학교 정보를 다시 입력하거나 Firebase 콘솔을 사용할 필요는 없다.

**코드를 내려받거나 Notion 수첩을 만든 것만으로 자동 갱신이 시작되지는 않는다.** 운영자의 Firebase 배포와 Notion 공개 연결 OAuth 설정, 교사의 연결 승인이 모두 필요하다. 배포된 서비스 주소가 없다면 상태는 준비 중이며 실제 예약 갱신을 완료했다고 안내하지 않는다.

## 교사가 하는 일

1. AI가 기존 설치 기록에서 학교·학년도와 실제 Notion 대상 ID를 확인한다. 이미 확인한 학교를 다시 묻지 않는다.
2. AI가 만든 연결 링크를 열고 학교·학년도·연결 대상을 확인한다.
3. Notion에서 이 서비스의 연결을 승인하고 **이미 만든 교무수첩 페이지**를 선택한다. 템플릿을 다시 복제하지 않는다.
4. 서버가 대상 페이지·블록·데이터 소스를 다시 확인하면 예약 갱신을 활성화한다. 첫 반영 결과와 마지막 성공 시각을 확인한다.

교사는 NEIS 키나 Notion 토큰을 직접 복사하지 않는다. 운영자가 관리하는 NEIS 키로 학교별 공개 자료를 조회한다. 교사의 Notion 접근 권한은 본인이 승인한 OAuth 연결에서 얻는다. AI의 MCP 연결 권한·토큰을 Firebase로 옮기거나 재사용하지 않는다.

Notion에서 선택한 상위 페이지는 하위 페이지에 대한 접근도 허용할 수 있다. 학생 기록까지 들어 있는 수첩 루트를 선택했다면 그 접근 범위를 확인해야 한다. 서비스가 사용하는 쓰기 대상은 등록한 급식·상태 블록과 업무·일정 원본이지만, 이를 Notion 자체의 행·뷰별 접근 권한 분리로 설명하지 않는다. [Notion 공개 연결과 승인](https://developers.notion.com/guides/get-started/authorization)

## 기존 설치 정보를 전달하는 방식

설치 담당자는 아래 허용된 메타데이터만 전달한다. 실제 값은 비공개 설치 기록에서 가져오며 공개 예시나 Git에 넣지 않는다.

| 필드 | 의미 |
| --- | --- |
| `version` | 형식 버전. 현재 `1` |
| `office_code` | NEIS 시도교육청 코드 |
| `school_code` | NEIS 표준학교코드. 컴시간 코드와 다름 |
| `school_name` | 확인한 학교 이름 |
| `academic_year` | 동기화할 학년도 |
| `root_page_id` | 기존 교무수첩 루트 페이지 ID |
| `agenda_data_source_id` | 기존 업무·일정 **원본 data source** ID |
| `meals_block_id` | 홈의 연동 전용 급식 콜아웃 ID |
| `status_block_id` | 연동 상태 전용 콜아웃 ID |

학교·ID 묶음을 base64url로 인코딩해 배포된 서비스의 `/connect#…` 링크 조각에 담는다. base64url은 암호화가 아니다. 이 링크에는 토큰·NEIS 키·학생 정보·교직원 연락처·수첩 본문을 포함하지 않으며 개인 연결 링크도 공개하지 않는다. 전체 `.local/state.json` 또는 `.local/mcp-install.json`을 업로드하지 않는다.

링크의 메타데이터 자체는 권한이나 일회용 인증 코드가 아니다. 서버는 이 정보를 받은 뒤 만료되는 일회용 등록 상태를 만들고 Notion OAuth 승인 결과에 연결한다. 사용자가 승인하지 않은 링크 열기만으로 동기화를 활성화하지 않는다. 서버는 링크의 ID를 신뢰해 바로 쓰지 않고, 새 OAuth 권한으로 대상과 스키마를 다시 조회해 확인한다.

MCP 설치 기록과 Python 설치 상태는 계속 분리한다. 클라우드는 **명시적인 새 등록**을 통해 별도 실행 상태를 만든다. 기존 연속 행사 페이지는 실제 외부 ID·기간·보관 상태를 읽어 확인한 뒤 재사용하며, MCP 상태를 Python 상태처럼 꾸며 넣지 않는다. 활성 중복 페이지나 모호한 기간이 있으면 자동 통합하지 않고 등록 또는 동기화를 중단해 확인한다.

### 설치 AI가 연결 링크 만들기

아래 주소의 `<실제_서비스_호스트>`는 운영자가 배포·확인한 호스트로 바꾼다. 아직 배포하지 않았다면 연결 정보를 비공개로 준비하고 링크 생성·등록은 보류한다. 서비스 주소는 경로·쿼리 없는 HTTPS origin이다.

```bash
# MCP 설치: 기록한 실제 ID로 만든 manifest만 사용한다.
python -m teacher_planner cloud-connect \
  --manifest .local/cloud-manifest.json \
  --service-url 'https://<실제_서비스_호스트>'

# REST 설치: 완료된 기존 상태와 이미 확인한 학교 JSON을 재사용한다.
python -m teacher_planner cloud-connect \
  --state .local/state.json \
  --school-config .local/neis-calendar.json \
  --status-block-id '<이미_만든_상태_블록_UUID>' \
  --service-url 'https://<실제_서비스_호스트>' \
  --output .local/cloud-manifest.json
```

`--school-config`와 `--status-block-id`는 REST `--state` 방식에서만 사용한다. 상태에 `config.school`, `cloud_sync.status_block_id`가 이미 기록되어 있다면 해당 옵션을 생략할 수 있다. 학교 JSON은 기존 NEIS 조회 결과 또는 확인한 학교 설정을 사용한다. 누락 정보를 임의로 추정하지 않는다. 이 명령은 상태 블록을 새로 만들거나 기존 설치 상태를 수정하지 않는다.

급식·상태 대상은 **자식 블록이 없는 연동 전용 콜아웃**이어야 한다. 기본 설치 기록에 상태 콜아웃이 없으면 설치 담당자가 허용된 수첩 안에 만들고 ID를 비공개로 기록한다. 기존 MCP 급식 영역에 날짜별 접기 블록·수동 메모 등 자식이 있다면 내용을 먼저 확인하고 보존한다. 요청받은 클라우드 전환 범위에서 새 전용 급식 콜아웃을 준비해 manifest에 기록하며, 기존 자식이나 메모를 자동 삭제하지 않는다. 새로운 화면 위치·기존 영역 처리 결과는 실제 Notion에서 확인한다. 배포나 `cloud-connect`가 이 준비 작업을 대신하지 않는다.

명령은 네트워크 요청이나 Notion 쓰기를 수행하지 않는다. 반환되는 `enrollment_url`을 교사에게 전달하며 `connected=false`, `daily_sync_enabled=false`는 정상적인 준비 상태다. `--output`은 정규화한 9개 필드만 비공개 JSON에 저장하며 입력 manifest·설치 상태·학교 JSON과 다른 경로여야 한다. 실제 연결·일일 갱신 활성 여부는 OAuth 후 서비스 응답으로 확인한다.

## 매일 실행되는 작업

기본 예약은 **매일 오전 7시부터 순차 실행, `Asia/Seoul`**이다. Firebase 예약 함수가 작업을 만들고 첫 작업을 한 시간에 걸쳐 분산한다. Cloud Tasks는 동시에 실행할 작업 수와 재시도를 제한하므로 모든 수첩이 정확히 7시에 갱신되거나 8시까지 완료된다는 뜻은 아니다. 예약 함수는 중복 실행될 수 있으므로 잠금·작업 상태·외부 ID를 사용해 같은 일정이 반복 생성되지 않게 한다. [Firebase 예약 함수](https://firebase.google.com/docs/functions/schedule-functions), [작업 큐 함수](https://firebase.google.com/docs/functions/task-functions)

- NEIS 자료는 학교·조회 범위별로 캐시해 같은 학교의 교사마다 다시 조회하지 않는다. 급식은 실행일의 서울 날짜, 학사일정은 등록한 학년도 범위를 사용한다.
- 학교 자료가 같아도 각 교사의 Notion 반영은 별도 작업이다. Notion 쓰기를 직렬화·제한하고 변경된 내용만 반영한다. 작업 큐의 제한은 Notion의 연결·워크스페이스 제한을 대신하지 않는다.
- 연속 학사일정은 기존 기간 페이지를 재사용한다. 누락된 행사를 자동 삭제하거나 수동 메모·관계를 덮어쓰지 않는다. 모호한 기간 축소·분리·중복은 자동 수정하지 않는다.
- 급식은 기존 홈 블록에 표시하고 캘린더·To-Do에 새 행을 만들지 않는다. 조회 오류·잘못된 학교·날짜 불일치 응답으로 마지막 정상 내용을 지우지 않는다. 정상 조회에서 공개 식단이 없는 경우는 자료 없음으로 구분한다.
- 재시도는 횟수와 대기 시간을 제한한다. 권한 해제·대상 삭제·스키마 변경 등 확인이 필요한 실패는 성공으로 표시하지 않는다.

이 서비스는 NEIS 공개 학사일정과 급식만 다룬다. 컴시간 시간표, 학생 출결·성적, 상담 기록, 자동 메시지 발송은 이 클라우드 예약에 포함하지 않는다. 학년도 변경도 자동으로 추정하지 않는다.

## 상태 확인과 중지

연결 화면에서 준비 중, 연결됨, 일시 중지 상태와 마지막 성공 시각을 구분한다. ‘연결됨’ 배지는 예약 대상이라는 뜻이며 첫 동기화가 성공했다는 뜻은 아니다. 대기·갱신 중·오류 메시지와 마지막 성공 시각을 함께 확인하고, 실패한 날을 새 성공 시각으로 기록하지 않는다. 서버의 `active` 상태는 반영을 완료한 뒤 저장된다. 브라우저 관리 세션이 만료되면 수첩의 연결·관리 링크를 열어 Notion 승인을 다시 진행한다.

일시 중지하면 이후 예약 반영을 멈춘다. Notion 연결 권한을 해제하면 서버의 추가 접근도 차단된다. 이미 Notion에 반영한 식단·일정은 그대로 남으며, 중지나 오류를 이유로 수첩을 삭제하지 않는다. 배포 전에는 이 관리 화면이나 상태 변경이 실제로 작동한다고 안내하지 않는다.

로컬 `neis-sync --watch`와 `meals-sync --watch`는 컴퓨터에서 프로세스가 실행되는 동안만 반복하는 기존 기능이다. Firebase 예약과 별개이며, 같은 수첩에 둘을 중복 실행하지 않는다. 서비스로 전환할 때는 기존 로컬 반복 실행을 중지한다.

## 운영자의 배포 전 준비

교사가 아니라 **서비스 운영자**가 한 번 준비한다.

1. 교무수첩 서비스 전용 Firebase 프로젝트와 결제 계정을 연결하고 Blaze 요금제로 설정한다. 함수의 운영 배포에는 Blaze가 필요하며 무료 사용량이 있더라도 모든 사용이 무조건 무료라는 뜻은 아니다. 기존 앱의 프로젝트에 Firestore 규칙을 덮어 배포하지 않는다. [Firebase 시작 안내](https://firebase.google.com/docs/functions/get-started)
2. Firebase CLI·Google Cloud CLI와 프로젝트 배포 권한을 준비한다. Cloud Functions, Cloud Run, Cloud Scheduler, Cloud Tasks, Secret Manager 등 배포에 필요한 API와 서비스 계정 권한을 확인한다.
3. Notion 공개 OAuth 연결을 만들고 필요한 읽기·삽입·수정 기능, 배포 서비스의 정확한 콜백 주소를 설정한다. 기존 수첩에 연결하므로 OAuth 템플릿 복제는 사용하지 않는다. [Notion 공개 연결](https://developers.notion.com/guides/get-started/public-connections)
4. 기본 Firestore 데이터베이스를 만들고 NEIS 인증키와 Notion OAuth 클라이언트 비밀값, 토큰 저장용 암호화 키를 Secret Manager에 넣는다. 함수 환경에는 공개 클라이언트 ID와 실제 서비스 기본 URL을 설정한다.
5. 교사 등록·OAuth·예약·작업 큐·오류 복구를 운영자 테스트 수첩에서 확인한 뒤 서비스 주소를 안내한다. 설치 성공, OAuth 승인 성공, 최초 동기화 성공을 각각 확인한다.

필수 설정은 다음과 같다. 실제 비밀값은 채팅·명령 인수·공개 저장소에 기록하지 않는다.

| 위치 | 이름 | 용도 |
| --- | --- | --- |
| Secret Manager | `NEIS_API_KEY` | 공개 학교 자료 조회 |
| Secret Manager | `NOTION_CLIENT_SECRET` | OAuth 코드 교환 |
| Secret Manager | `TOKEN_ENCRYPTION_KEY` | Fernet의 URL-safe base64 32바이트 키. 저장한 Notion 토큰 암호화 |
| 함수 환경 | `NOTION_CLIENT_ID` | 공개 OAuth 클라이언트 ID |
| 함수 환경 | `PUBLIC_BASE_URL` | 실제 배포된 서비스의 HTTPS 기본 URL |

### 프로젝트와 비밀값 설정

Firebase CLI의 비밀값 입력은 대화형 명령을 사용한다. 아래 `<프로젝트_ID>`는 실제 운영자의 프로젝트 ID로 바꾼다. 새 프로젝트가 정해지기 전에는 실행하지 않는다. Firebase 로그인·Google Cloud 로그인과 기본 Firestore 생성은 운영자 계정으로 완료한다.

```bash
gcloud services enable secretmanager.googleapis.com --project '<프로젝트_ID>'
firebase functions:secrets:set NEIS_API_KEY --project '<프로젝트_ID>'
firebase functions:secrets:set NOTION_CLIENT_SECRET --project '<프로젝트_ID>'
firebase functions:secrets:set TOKEN_ENCRYPTION_KEY --project '<프로젝트_ID>'
```

비밀값을 바꾸면 해당 비밀을 사용하는 함수를 다시 배포한다. 함수 코드에서 명시적으로 연결한 비밀만 런타임에서 사용할 수 있다. [Firebase 비밀 설정](https://firebase.google.com/docs/functions/config-env)

`TOKEN_ENCRYPTION_KEY`는 `cryptography.fernet.Fernet.generate_key()`로 생성한 값을 비밀 관리 도구에 보관해 입력한다. 비밀값을 콘솔 로그에 출력하거나 공개 설정 파일에 저장하지 않는다. 이미 등록한 토큰이 있을 때 암호화 키만 교체하면 기존 암호문을 읽을 수 없으므로 먼저 별도 키 교체·재연결 절차를 마련한다.

### 런타임과 Hosting 배포

작성 원본은 저장소 루트의 `main.py`, `cloud/`, `teacher_planner/`이며 의존성은 `requirements.txt`에 있다. **Firebase 함수의 배포 소스는 `.local/firebase/functions`**다. `scripts/build_cloud.py`가 허용된 파일만 이 디렉터리에 준비한다. 복사 대상은 `main.py`, `requirements.txt`, `cloud/*.py`, `teacher_planner/*.py`와 패키지의 설계 JSON 두 개이며, 루트 `.env`나 설치 상태·학교 조회 파일은 복사하지 않는다.

`firebase.json`의 predeploy도 같은 준비 스크립트를 실행한다. 준비 스크립트는 배포용 `venv`와 허용된 매개변수 파일을 유지한다. 웹 연결 화면은 `cloud/web/`만 Firebase Hosting에 배포한다. 저장소 루트를 함수 소스로 되돌리거나 `.local/`를 Hosting 공개 디렉터리로 바꾸지 않는다.

| 함수 | 역할 |
| --- | --- |
| `planner_api` | 등록 시작, OAuth 콜백, 상태 조회, 일시 중지·재개 |
| `planner_daily` | 매일 07:00 `Asia/Seoul`에 활성 수첩 작업 등록 |
| `planner_sync_task` | Cloud Tasks에서 수첩 한 건의 급식·학사일정 동기화 |

함수 리전은 `asia-northeast3`다. Hosting은 `/api/**`를 `planner_api`로 보내고 `/connect`에 연결 화면을 제공한다. `PUBLIC_BASE_URL`에는 실제 프로젝트의 `https://<프로젝트_ID>.web.app` 또는 설정한 사용자 도메인을 넣고, Notion OAuth Redirect URI에는 **같은 기본 URL의 `/api/callback`**을 정확히 등록한다. 이 문자열의 프로젝트 ID는 예시 자리표시자이며 운영 서비스 주소가 아니다. `NOTION_CLIENT_ID`도 해당 공개 연결의 실제 값으로 설정한다.

저장소 루트에서 먼저 배포 파일을 준비한 뒤, **준비된 함수 소스 안에** 배포용 Python 환경을 만들고 배포한다. Firebase CLI가 이 환경으로 함수를 탐색한다. 기본 로컬 설치 CLI에는 이러한 클라우드 패키지가 필요하지 않다.

```bash
python3 scripts/build_cloud.py
python3.12 -m venv .local/firebase/functions/venv
.local/firebase/functions/venv/bin/python -m pip install \
  -r .local/firebase/functions/requirements.txt
firebase deploy --only functions,firestore:rules,hosting --project '<프로젝트_ID>'
```

배포 시 Firebase CLI의 함수 매개변수 입력에서 `NOTION_CLIENT_ID`, `PUBLIC_BASE_URL`을 설정한다. 직접 준비한다면 `.local/firebase/functions/.env.<프로젝트_ID>`에 **이 두 비밀이 아닌 매개변수만** 넣는다. 루트 `.env`를 이곳으로 복사하지 않으며 `NEIS_API_KEY`, `NOTION_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`는 계속 Secret Manager로만 전달한다. 생성된 프로젝트별 환경 파일도 Git에 올리지 않는다. 배포 성공 후 실제 함수 URL·Hosting 경로·Notion 콜백 주소가 일치하는지 확인한다.

로컬 클라우드 테스트용 환경을 별도로 둘 경우 `.local/cloud-venv` 등을 사용한다. 테스트용 환경과 Firebase 함수 탐색에 필요한 `.local/firebase/functions/venv`는 구분한다.

### 작업 큐 권한과 캐시 정리

현재 작업 큐는 **시범 운영용**으로 전체 서비스에서 한 작업자만 한 번에 처리한다. 큐는 초당 최대 한 작업을 전달하고 각 작업의 시도 횟수는 최대 8회, 재시도 대기는 60~900초로 제한한다. 학사일정은 한 작업에 최대 8건씩 반영하고 남은 작업을 다시 예약한다. 수천·수만 교사의 당일 처리 시간은 검증하지 않았으며, 규모를 늘리려면 대기열·실행 시간과 Notion 연결·워크스페이스 한도를 측정한 뒤 분할·병렬 처리 설계를 추가해야 한다.

배포한 함수의 실제 런타임 서비스 계정과 작업자가 실행되는 Cloud Run 서비스 이름을 확인한다. 아래 `<런타임_서비스_계정>`과 `<작업자_Cloud_Run_서비스>`는 조회한 값으로 바꾸며 기본 계정명을 추정하지 않는다. enqueue 함수와 작업의 호출 계정이 다르면 각 계정의 역할에 맞게 적용한다.

```bash
gcloud projects add-iam-policy-binding '<프로젝트_ID>' \
  --member='serviceAccount:<런타임_서비스_계정>' \
  --role=roles/cloudtasks.enqueuer
gcloud iam service-accounts add-iam-policy-binding '<런타임_서비스_계정>' \
  --project='<프로젝트_ID>' \
  --member='serviceAccount:<런타임_서비스_계정>' \
  --role=roles/iam.serviceAccountUser
gcloud run services add-iam-policy-binding '<작업자_Cloud_Run_서비스>' \
  --region=asia-northeast3 --project='<프로젝트_ID>' \
  --member='serviceAccount:<런타임_서비스_계정>' \
  --role=roles/run.invoker
```

Cloud Tasks에 작업을 넣는 권한, 작업 호출 계정을 사용할 `actAs` 권한, 2세대 작업자 호출 권한을 구분한다. 작업자 함수를 누구나 호출할 수 있도록 공개하지 않는다. [Firebase 작업 큐 IAM](https://firebase.google.com/docs/functions/task-functions#iam_permissions), [2세대 함수 호출 인증](https://docs.cloud.google.com/functions/docs/securing/authenticating)

코드의 `invoker='private'`만으로 배포된 IAM 검증이 끝난 것은 아니다. 현재 Python SDK는 작업 큐 함수의 이 설정을 배포 manifest에 출력하지 않으므로, 배포 후 실제 작업자 정책에 `allUsers`·`allAuthenticatedUsers` 호출 권한이 없는지 확인하고 인증 없는 호출이 거부되는지 검증한다. 아래 명령은 정책을 읽기만 한다. 기존 공개 함수에 같은 이름으로 덮어 배포하지 않는다.

```bash
gcloud run services get-iam-policy '<작업자_Cloud_Run_서비스>' \
  --region=asia-northeast3 --project='<프로젝트_ID>'
```

학교 캐시는 `planner_school_cache` 컬렉션에 저장하며 `expires_at`에 3일 뒤의 정리 시각을 기록한다. 이 필드만 기록한다고 자동 삭제되지 않으므로 운영자는 TTL 정책도 설정한다. 만료 삭제는 즉시 완료되지 않을 수 있다. [Firestore TTL](https://firebase.google.com/docs/firestore/ttl)

```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=planner_school_cache --database='(default)' \
  --enable-ttl --project='<프로젝트_ID>'
```

운영 프로젝트가 없는 상태에서 예시 주소를 작동하는 서비스로 안내하거나 교사를 자동 등록하지 않는다. 실제 검증은 테스트 수첩의 OAuth 승인 → 작업 생성 → NEIS 조회 → Notion 반영 → 같은 작업 재실행 시 중복 없음 → 일시 중지 순서로 확인한다. 로컬 단위 테스트는 이 운영 검증을 대신하지 않는다.

## Notion의 기본 자동화로 대체할 수 있나요?

Notion 유료 플랜의 데이터베이스 자동화는 반복 실행(`Every day`)과 `Send webhook` POST 동작을 제공한다. 이를 서버 호출의 시작 신호로 사용할 수는 있다. 그러나 웹훅 자체가 NEIS를 조회하거나 응답을 수첩에 쓰지는 않으므로 인증·캐시·Notion 쓰기를 담당할 서버는 여전히 필요하다. **이 저장소는 Notion 기본 자동화나 웹훅 수신기를 설치하지 않는다.** 기본 실행 방식은 Firebase 예약이며, 웹훅을 쓰려면 인증된 별도 수신 경로를 구현하고 교사가 원하는 자동화를 수동으로 설정·검증해야 한다. [데이터베이스 자동화](https://www.notion.com/help/database-automations), [웹훅 동작](https://www.notion.com/help/webhook-actions)

**Notion 무료 플랜에서는 Firebase 쪽 예약을 사용하면 된다.** Notion의 유료 기본 자동화 기능이 없어도 서버가 승인된 OAuth 권한으로 기존 페이지를 갱신할 수 있다. Firebase 운영 비용과 Notion 요금제는 별개다.

## 공개 저장소에 포함하지 않는 정보

실제 연결 링크, 설치 ID 묶음, 교사별 매핑, Notion OAuth 토큰과 갱신 토큰, 암호화 키, NEIS 키는 공개 Git·로그·스크린샷에 넣지 않는다. 클라우드 상태와 캐시의 접근 권한도 서버에 한정한다. 학생·상담·연락 원문은 이 서비스의 등록 입력이나 학교 캐시에 넣지 않는다.
