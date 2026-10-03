# 📒 나의 미니 교무수첩

made by 여광재(온양고) · made with [DoRms](https://dorms.school)

**이 저장소를 자신의 생성형 AI에 주면, 교사에게 필요한 설정을 묻고 개인 Notion에 교무수첩을 만들도록 안내합니다.** 한국어 설명, 데이터베이스 설계, AI 실행 지침, Python 설치 도구를 함께 제공합니다.

기본 템플릿은 **대시보드·학급 경영·교과 진도·학사 일정**의 네 Notion 페이지로 구성됩니다. 오늘 필요한 기록을 대시보드에서 확인하고 각 업무 페이지로 이동합니다. **수첩은 학년도마다 한 권**을 사용합니다. 해당 연도 3월부터 다음 해 2월까지의 기록을 담고, 2학기에도 같은 수첩을 이어 씁니다. 수업 진도의 학기는 필요한 기록에서 선택해 구분합니다.

교사용 사용 안내서: [웹에서 읽기](https://notion-teacher-planner.notion-teacher-planner-cloudflare.workers.dev/guide) · [PDF 바로 보기](https://notion-teacher-planner.notion-teacher-planner-cloudflare.workers.dev/teacher-planner-guide.pdf) · [첨부용 HTML](output/html/교무수첩_사용안내서.html) · [수정용 Word](output/docx/교무수첩_사용안내서.docx). 웹 질문지로 시작하는 방법, 학년도 한 권 운영, 학생별 상담 이력, 여섯 가지 기록 양식, 시간표·진도, 캘린더·PARA, 급식·학사일정 갱신, 문제 해결과 기존 수첩의 선택 업데이트를 설명합니다. 문서 버전은 1.3, 기준일은 2026년 10월 2일입니다.

커뮤니티 공유용 [개조식 소개글](output/community/교무수첩_커뮤니티_소개글.txt)과 [첨부용 PDF](output/pdf/교무수첩_사용안내서.pdf)도 제공합니다. HTML 안내서는 별도 로그인 없이 읽을 수 있으며, 웹에서 PDF를 열거나 HTML·PDF 파일을 내려받을 수 있습니다.

이미 만든 수첩은 [선택 업데이트 안내](docs/UPDATES.md)를 사용합니다. 첫 업데이트는 학생별 상담 이력 연결(`student-history-v1`)이며, 적용한 기능과 남은 화면 설정을 각각 기록합니다.

> 오늘의 수업과 꼭 해야 할 일을 한곳에. 작은 기록으로 가볍게 시작합니다.

## 웹에서 설치 설정 준비하기

[교무수첩 시작하기](https://notion-teacher-planner.notion-teacher-planner-cloudflare.workers.dev/setup)에서 공통 질문을 **5단계**로 답하고, 내 설정이 담긴 AI 설치 요청문을 복사할 수 있습니다. Q01·Q02 → Q03·Q04 → Q05·Q06 → Q07·Q08 → Q09·Q10 순서이며 필요한 추가 질문만 표시합니다.

요청문은 브라우저 안에서 정해진 규칙으로 생성합니다. 답변을 서버에 제출하거나 LLM API를 호출하지 않아 **웹 설문 자체의 AI API 비용은 없습니다.** 입력 초안은 현재 탭의 `sessionStorage`에 임시 저장되며 화면에서 초기화할 수 있습니다. 토큰·비밀번호·학생 명단·상담 내용은 입력하지 마세요.

마지막에 복사한 요청문을 **Notion 쓰기 도구가 연결된 AI**에 붙여 넣어 설치를 진행하세요. 웹 양식은 Notion 페이지를 생성하거나 연동을 활성화하지 않습니다. AI 서비스의 이용 조건과 비용은 해당 서비스에 따릅니다. 자세한 사용법은 [웹 시작 안내](docs/WEB_SETUP.md)에 있습니다.

## AI에게 이렇게 말하세요

```text
https://github.com/RuneDaeg/notion-teacher-planner 를 읽고
START_HERE.md 순서대로 내 Notion에 미니 교무수첩을 만들어줘.
docs/ONBOARDING.md의 공통 질문 문구·선택지·순서에 따라
이미 답한 내용은 건너뛰고 필요한 질문만 2~3개씩 물어봐줘.
Notion 연결을 확인하고 내가 지정한 페이지 아래에 만들어줘.
teacher_planner/layout_contract.json의 행·열·폭·접기를 그대로 재현하고,
네 페이지 모두 전체 너비로 만들어 실제 화면까지 검증해줘.
```

[공통 설치 질문지](docs/ONBOARDING.md)에 학교·담당 수업·생성 위치·추가 기능·양식·시간표·급식과 학사일정의 질문을 미리 정해 두었습니다. 웹 화면과 AI 대화는 같은 질문 ID·문구·선택지·분기 규칙을 사용합니다. 웹에서 만든 요청문을 받으면 AI는 이미 답한 질문을 건너뛰고 확인이 필요한 값만 이어서 묻습니다. 질문지 버전과 답변을 비공개로 기록해 재사용하며, 실제 설치는 AI의 실행 도구와 권한을 확인한 뒤 진행합니다.

AI가 GitHub를 읽을 수 없다면 저장소를 ZIP으로 내려받아 첨부하세요. 채팅 전용 AI는 설계 안내까지 가능하며, **실제 생성에는 Notion 쓰기 도구(MCP/커넥터) 또는 아래 Python 실행 환경과 Notion 연결 토큰이 필요합니다.** 도구가 없는데 생성했다고 답하지 않도록 실행 지침을 포함했습니다.

## 무엇이 들어가나요?

| 구성 | 사용 방법 |
| --- | --- |
| 오늘의 중식 | NEIS 중식 메뉴·열량만 홈에 표시, 날짜·확인 시각·알레르기 번호 보존 |
| 기록 양식 | 상담·학부모 연락·회의·수업·평가·조회/종례의 복사용 빈 페이지 |
| 학생 명단 | 학급·번호·학생 ID로 명단 관리 |
| 상담 기록 | 학생 연결, 관찰 사실, 합의 사항, 후속 확인 |
| To Do List | P1~P4, 업무 분류, 상태, 다음 행동, 마감 |
| 주간·월간 캘린더 | 주간·월간·마감 탭, 선택형 NEIS 공개 학사일정 가져오기 |
| 교사 시간표 | 날짜·교시·교과·반, CSV/JSON 가져오기, 선택형 컴시간 웹 조회·동기화 |
| 수업 진도 | 교과별·반별 단원, 계획/완료 차시, 진도율, 다음 수업 |
| 교사용 PARA | 프로젝트, 담당 영역, 자료, 보관 + 수집함 |
| 선택: 출결·제출물 | 학생별 출결과 제출 상태 체크 |
| 선택: 평가·채점 | 평가 일정, 채점 마감, 채점 완료 인원·진행률 |
| 선택: 학부모 연락 | 연락 요지, 합의 사항, 후속 업무 |
| 선택: 회의록 | 안건, 결정, 담당, 후속 업무 |
| 선택: 교직원 연락망 | 부서·직책·교내 연락처·이메일·담당 업무 |
| 선택: 학교 계정 관리 | 서비스 접속 주소·계정 ID·관리 담당·비밀번호 관리 도구 링크 |

기본 9개, 선택 기능을 모두 켜면 **16개 원본 데이터베이스**를 사용합니다. 네 페이지는 같은 원본을 연결하며 학생·수업·일정을 중복 저장하지 않습니다. 전체 선택 시 24개 연결 DB 블록에 46개 연결 뷰를 탭으로 배치합니다. 원본 뷰를 포함한 정확한 구성은 `plan` 명령에서 확인할 수 있습니다. 이 저장소 제작 요청에서는 추가 기능을 모두 선택했지만, 재사용할 때는 각 교사에게 다시 묻습니다. 학교 계정에는 비밀번호·복구 코드·토큰 필드를 만들지 않습니다.

```text
📒 2026학년도 · 교무수첩 데스크 (대시보드)
 ├─ 학급 경영 & 학생 상담     명렬표·학생 카드·관찰·출결·상담·연락·제출
 ├─ 교과 진도표 & 시간표      요일×교시 주간표·반/교과/학기 진도·평가
 ├─ 학사 캘린더 & PARA        주간/월간/마감 탭·프로젝트·영역·자료·보관
 ├─ 양식 모음                선택한 기록 양식
 ├─ 운영 자료                업무 원본 DB
 └─ 학생 기록                학생 기록 원본 DB
```

모든 페이지에 같은 이동 링크가 있습니다. 할 일·학생·진도는 보기 탭으로 전환하고, **주간·월간 캘린더도 한 연결 DB 안의 탭**으로 사용합니다. 대시보드와 학사 캘린더 페이지의 연결 블록은 같은 업무·일정 원본을 공유합니다. 마감 탭은 같은 원본의 `마감`, 주간·월간은 `일정` 속성을 사용합니다.

홈과 교과 페이지의 기본 교사 시간표는 **교시·수업시간을 세로로, 월~금을 가로로 둔 같은 주간표**입니다. 교과 페이지의 표와 표시 주간을 Notion 동기화 블록으로 홈의 `오늘의 수업 · 주간 시간표`에 공유합니다. 두 화면은 같은 교사 시간표 DB의 같은 주 기록을 표시합니다. 교과의 `시간표 원본 · 관리용`과 홈의 관리용 구역에는 원본 DB 목록과 오늘 수업 필터 보기를 보존하며, 기본적으로 닫힌 접기 안에 둡니다. 도구가 지원하지 않는 설정은 실제 Notion UI에서 마무리하고 검증합니다. 파일·컴시간 동기화는 원본 시간표를 반영한 뒤 두 화면의 표시 주간과 주간표를 함께 갱신하며, 업무·일정·To-Do나 학사 캘린더에 수업을 넣지 않습니다. 표시용 주간표를 직접 편집하면 두 화면에 보이지만 원본 DB로 역전송되지는 않습니다. 조회·종례 관찰 메모는 수동으로 작성합니다. 요약 숫자나 실시간 연동 표시를 임의로 채우지 않습니다. 공개 학사일정은 NEIS API에서 가져올 수 있습니다. 공식 NEIS 출결·학적 쓰기, 푸시 동기화·자동 메시지 발송은 제공하지 않습니다.

[디자인 기준](DESIGN.md)은 요청받은 색상·서체·화면 비율과 Notion 네이티브 구현 범위를 구분합니다. 정확한 1440px 화면·240px 사이드바·사용자 지정 CSS를 Notion에 고정하지 않습니다. 네 페이지는 **전체 너비 켜기·작은 텍스트 끄기**로 시작하며, **행·열·상대 폭·접기 구조는 [공통 배치 계약](teacher_planner/layout_contract.json)의 `teacher-desk-layout-v1`로 고정**합니다. 홈은 4열 빠른 실행, 중식/할 일 40/60, 출결/상담 50/50, 진도/평가 55/45, 전폭 시간표·캘린더와 하단 문서 보관실을 사용합니다. 선택하지 않은 기능만 생략하고 남은 열의 폭을 맞춥니다. 상세 배치와 교사별 설정은 [기본 템플릿 안내](docs/DEFAULT_TEMPLATE.md)를 참고하세요. 참고 이미지의 실제 이름·사진·전화번호·학교 링크는 기본값에 포함하지 않습니다.

## 화면 미리보기

아래는 네 페이지 구조를 보여 주는 설명용 빈 예시입니다. 실제 Notion 설치 화면이나 실데이터 캡처가 아니며, Notion의 서체·색상은 실제 환경에 따라 달라질 수 있습니다. 아래 설명용 이미지는 공통 배치 계약 검증의 기준 화면이 아니며, 실제 설치는 계약의 열 폭·접기 구조와 실제 화면 증거로 확인합니다.

| 대시보드 | 학급 경영·상담 | 교과 진도·시간표 | 학사 캘린더·PARA |
| --- | --- | --- | --- |
| [미리보기](docs/template-preview.png) | [미리보기](docs/classroom-preview.png) | [미리보기](docs/teaching-preview.png) | [미리보기](docs/planning-preview.png) |

## Python으로 설치하기

Python 3.10 이상. 외부 런타임 패키지 없이 저장소 폴더에서 실행합니다. Windows에서 IANA 시간대가 없다면 `python -m pip install tzdata`가 필요할 수 있습니다.

```bash
git clone https://github.com/RuneDaeg/notion-teacher-planner.git
cd notion-teacher-planner
mkdir -p .local
cp config.example.json .local/config.json
```

`.local/config.json`에 학년도·교사·반·교과·추가 모듈을 입력합니다. 새 설치에는 수첩 전체의 학기를 지정하지 않으며, 루트 제목은 `{academic_year}학년도 · {title}`로 표시합니다. 기존 설정의 `semester`는 호환성을 위해 그대로 두고 원래 설치 상태와 함께 재사용합니다. 학교급은 설명용 메타데이터입니다. 담임이 아니면 `homeroom`을 `false`로 설정합니다. 시연용 가상 기록을 원할 때만 `demo`를 `true`로 바꿉니다.

선택적인 `dashboard` 설정으로 즐겨찾기·외부 자료 링크·표시 교시 수를 지정할 수 있습니다. 공유 페이지·설문·NEIS·에듀파인 주소를 선택적으로 입력합니다. 비어 있는 링크는 설정 안내로 표시합니다. 이 바로가기 설정은 페이지·설문 생성이나 자동 로그인·데이터 연동을 수행하지 않으며, 학사일정 연동은 아래 별도 명령으로 실행합니다. [설정 예시](docs/DEFAULT_TEMPLATE.md#교사별-설정)를 확인하세요.

```bash
# 토큰 없이 설계와 입력값 검토 — Notion에 쓰지 않습니다.
python -m teacher_planner plan --config .local/config.json
python -m teacher_planner layout-plan --config .local/config.json
python -m teacher_planner install --config .local/config.json
```

[Notion 내부 연결](https://developers.notion.com/guides/get-started/create-a-notion-integration)을 만들고 읽기·삽입·수정 권한을 설정한 뒤, 대상 **일반 페이지**의 연결 메뉴에서 해당 연결을 추가합니다. 학생 기록을 쓸 경우 개인용 페이지에서 시작하세요. 토큰은 비밀 입력창 또는 로컬 환경 변수로만 제공합니다.

```bash
# 토큰이 이미 NOTION_TOKEN 환경 변수에 들어 있는 상태에서 실행
export NOTION_PARENT_PAGE_ID='대상_페이지의_URL_또는_UUID'
python -m teacher_planner install --config .local/config.json --apply
python -m teacher_planner verify --core-only
```

Python REST는 원본 DB·관계·뷰·시간표를 만드는 기능 설치 경로입니다. **다단 연결 DB, 전체 너비, 닫힌 접기와 열 폭은 지원되는 MCP/UI로 마무리하고 실제 화면을 확인해야 기본 템플릿 설치가 완료됩니다.** 세로로 나열한 중간 화면을 같은 완성본으로 안내하지 않습니다. [설치 확인 기준](docs/ACCEPTANCE.md)에 따라 네 페이지를 다시 조회하고 UI 증거를 비공개로 저장한 뒤 읽기 전용 배치 검사를 실행합니다.

```bash
python -m teacher_planner verify-layout --snapshot .local/layout-snapshot.json --report .local/layout-report.json
# 기능을 다시 조회하고 수집한 화면 증거와 함께 최종 판정
python -m teacher_planner verify --state .local/state.json --layout-snapshot .local/layout-snapshot.json
```

스냅샷은 실제 재조회·화면 확인에서 수집합니다. 이 명령이 Notion을 자동 촬영하거나 계획을 실제 화면으로 검증하는 것은 아닙니다. `verify --core-only`의 통과는 기능만 확인한 결과입니다. 기본 `verify`는 배치 증거가 없으면 배치 미완료로 종료하며, `--layout-snapshot`을 포함한 최종 검증에서 기능·배치를 함께 확인합니다. 기능 설치·배치 재현·클라우드 연결의 확인 결과를 각각 구분합니다.

토큰을 입력할 때 셸 기록에 실제 값을 남기지 않는 예:

```bash
# bash/zsh; 입력 내용은 화면에 표시되지 않습니다.
printf 'Notion token: '
read -r -s NOTION_TOKEN
export NOTION_TOKEN
printf '\n'
```

`.env.example`은 변수 이름 안내이며 `.env`를 자동 실행하거나 읽지 않습니다. 설치 상태와 ID는 `.local/state.json`에만 저장됩니다. 정상 설치 재실행은 생성한 객체를 재사용합니다. 다음 학년도 수첩을 만들 때는 새 설정과 `--state .local/2027-state.json`처럼 별도 상태 경로를 사용합니다. 기존 학년도 자료는 자동 변경하지 않습니다.

2학기가 되어도 같은 수첩과 원래 설정·설치 상태를 사용합니다. 제목에 `1학기`가 있는 기존 수첩도 계속 사용할 수 있습니다. 제목만 바꾸고 싶다면 기존 페이지의 제목 변경을 요청하세요. 제목 변경은 DB 구조 변경이 아니며, 학기 전환이나 제목 변경을 위해 수첩·기록을 복제하거나 설정·설치 상태를 초기화하지 않습니다.

새 기본 템플릿은 새 설치에 적용합니다. 이전 버전의 상태로 `install`을 실행하면 설계 변경을 감지해 중단하며 기존 페이지를 덮어쓰지 않습니다. 기존 수첩을 같은 배치로 맞추고 싶다면 [배치 수정 절차](docs/UPDATES.md#기존-수첩을-공통-배치로-맞추기)를 따라 기존 ID·기록을 유지하는 수정만 요청하세요. 배치 변경 때문에 새 수첩을 만들거나 상태를 초기화하지 않습니다. 기존 수첩의 시간표 가져오기·동기화는 기존 경로를 계속 사용합니다.

## 시간표 가져오기

```bash
# 날짜별 행으로 정리한 내 파일을 private/에 보관하세요.
python -m teacher_planner import-timetable examples/timetable.csv
python -m teacher_planner import-timetable private/timetable.csv --config .local/config.json --apply
```

컴시간 웹에서 조회할 수 있는 학교라면 **학교 코드와 교사 번호로 해당 교사의 이번 주 시간표**를 가져올 수도 있습니다. 별도 앱 설치는 필요하지 않습니다.

```bash
# 컴시간 조회만 수행하며 Notion 토큰은 필요하지 않습니다.
python -m teacher_planner comcigan-week --school-code 학교코드 --teacher-id 교사번호 --output .local/comcigan-week.json

# 기존 Notion 설정에 맞는지 검토한 뒤 반영합니다.
python -m teacher_planner comcigan-sync --school-code 학교코드 --teacher-id 교사번호 --config .local/config.json
python -m teacher_planner comcigan-sync --school-code 학교코드 --teacher-id 교사번호 --config .local/config.json --apply
```

학교 코드·교사 번호는 `COMCIGAN_SCHOOL_CODE`, `COMCIGAN_TEACHER_ID` 환경 변수로 대신 제공할 수 있습니다. `--date YYYY-MM-DD`로 조회할 주를 지정하며, 생략하면 서울 기준 이번 주입니다. Notion 반영에는 기존 설치 상태·반·교과 설정이 필요합니다. 교시 시각을 설정하면 시간표의 수업일에 정확한 시각을 기록하고 주간표의 수업시간에도 표시합니다. 생략하면 날짜만 기록하고 시간은 추정하지 않습니다. Python 설치는 원본 반영 뒤 소유한 주간표를 갱신하며, MCP 설치는 해당 경로의 설치 기록과 도구로 원본·주간표를 함께 갱신합니다. [설정과 동기화 사용법](docs/COMCIGAN.md)을 먼저 확인하세요. 이전 버전에서 업무·일정에 복사했던 수업은 자동 삭제하지 않으며, 필요한 정리는 대상 기록을 확인한 별도 작업으로 진행합니다.

이 기능은 **공식 API가 아닌 선택형 웹 연동**입니다. `--watch --interval 600 --apply`로 실행 중인 프로세스에서 10분마다 갱신할 수 있으며, 자동 예약 작업은 설치하지 않습니다. 웹 구조 변경·접근 제한·해당 주 미제공 시 오류로 중단하며 로그인이나 접근 제한을 우회하지 않습니다.

## 학교 학사일정 가져오기

**NEIS 공개 학사일정 API에서 학교 행사를 가져와 기존 Notion 캘린더에 추가할 수 있습니다.** NEIS 인증키를 `NEIS_API_KEY`에 설정하고 시도교육청 코드·표준학교코드를 사용합니다. 컴시간 학교 코드와는 다릅니다.

```bash
# 조회만 수행하고 비공개 JSON으로 검토한다.
python -m teacher_planner neis-calendar --office-code 시도교육청코드 --school-code 표준학교코드 --year 2026 --output .local/neis-calendar.json

# 기존 수첩 설정으로 검토한 뒤 --apply로 반영한다.
python -m teacher_planner neis-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json
python -m teacher_planner neis-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --apply
```

같은 학교·학년도·과정에서 같은 이름의 행사가 달력상 연속되면 **첫날~마지막 날의 행사 페이지 하나**로 묶습니다. 날짜별 설명·대상 학년은 보존하며, 중간 날짜가 비면 별도 행사로 둡니다. 기존 날짜별 페이지를 묶는 Python 작업은 `--merge-existing`을 추가하고 원래 페이지는 삭제 없이 보관합니다. 같은 기간을 다시 조회하면 대표 페이지를 재사용합니다. 누락된 행사를 자동 삭제·취소하지 않습니다. `--watch --interval 21600 --apply`로 실행 중인 프로세스에서 6시간마다 조회할 수 있습니다. [인증키·기간·병합 규칙](docs/SCHOOL_CALENDAR.md)을 확인하세요.

## 오늘 급식과 기록 양식

홈의 `오늘의 중식` 영역에 NEIS 중식 메뉴·열량만 표시합니다. 날짜·학교·확인 시각·알레르기 번호·출처를 유지하고 조식·석식·원산지·영양정보는 원본 JSON에 보존합니다. 중식이 없는 날은 공개 중식 정보 없음으로 표시하며 다른 식사로 대신하지 않습니다. 학사일정과 같은 `NEIS_API_KEY`, 시도교육청 코드·표준학교코드를 사용하며 업무·일정이나 시간표에 행을 만들지 않습니다.

```bash
# 기존 수첩에 급식 영역과 선택한 양식 모음 추가
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json --apply

# 오늘 식단 조회 후 홈에 반영
python -m teacher_planner meals-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --apply
```

새 설치에는 급식 안내 영역과 현재 설정에 맞는 양식이 포함됩니다. `setup-extras`는 기존 수첩의 페이지·DB·기록을 다시 만들지 않습니다. 급식 조회는 별도로 실행하며 `--watch --interval 21600 --apply`로 실행 중 6시간마다 갱신하고 날짜가 바뀌면 새 날짜를 조회합니다. 프로세스가 멈추면 마지막 날짜의 표시가 남습니다. [급식 설정](docs/MEALS.md)을 확인하세요.

Python 설치의 `양식 모음`은 상담·학부모 연락·회의록·수업·평가·조회/종례의 **복사용 빈 페이지 양식**입니다. `forms`로 선택하고 본문을 새 기록에 복사합니다. DB의 `새로 만들기`에서 바로 선택하려면 AI에게 **네이티브 템플릿 등록**을 요청할 수 있습니다. AI가 기존 대상 DB의 템플릿 편집 화면에서 등록하고 드롭다운을 확인하는 별도 절차이며, Python `setup-extras`가 자동 등록하지는 않습니다. [대상 DB와 등록·사용법](docs/FORMS.md)을 참고하세요.

## 컴퓨터를 꺼도 매일 자동 갱신하기

권장 경로는 **Cloudflare Workers Free + D1 + Cron**입니다. **매일 오전 7시 이후(한국 시간)** NEIS 급식·학사일정을 기존 Notion 수첩에 순차 반영합니다. 매분 실행에서 작은 작업을 이어 처리하고 수첩별로 하루 한 번 완료하도록 구성합니다. 학교별 조회 결과를 함께 사용하며, 교무수첩의 화면과 기록은 계속 Notion 안에 남습니다.

설치 AI가 이미 받은 학교·학년도와 실제 페이지 ID로 연결 링크를 준비하면, 교사는 링크에서 **Notion 연결을 승인**합니다. 학교를 다시 입력하거나 NEIS 키를 발급받거나 클라우드 콘솔을 열 필요가 없습니다. AI의 MCP 권한과 클라우드의 OAuth 권한은 별개이며, 기존 수첩을 선택해 연결합니다.

**현재 코드 제공 단계이며 운영 계정 설정·배포·OAuth 등록 전에는 예약 갱신이 켜지지 않습니다.** 기본 시범 운영 상한은 수첩 50개, 서울 날짜 기준 하루 작업 1,000단계이며 무료 한도 안에서 실제 사용량을 검증해야 합니다. 모든 규모에서 무료이거나 이 설정이 결제 차단 한도라는 뜻은 아닙니다. 운영자가 Cloudflare 계정·D1·Notion 공개 연결·NEIS 키를 준비합니다. Notion 무료 플랜도 사용할 수 있고 로컬 `--watch`와는 별개입니다. [공통 연결](docs/CLOUD_SYNC.md), [Cloudflare 배포](docs/CLOUDFLARE.md)를 확인하세요. Blaze가 필요한 [Firebase 경로](docs/FIREBASE.md)도 선택형으로 유지합니다.

## AI와 개발자를 위한 안내

- [START_HERE.md](START_HERE.md): AI가 읽을 첫 문서
- [ONBOARDING.md](docs/ONBOARDING.md): AI 공통 질문 원문·선택지·순서·조건과 설정 매핑
- [WEB_SETUP.md](docs/WEB_SETUP.md): 5단계 웹 질문지·AI 요청문 복사·임시 초안 보관
- [AGENTS.md](AGENTS.md): 실행 범위와 데이터 취급 원칙
- [INSTALL_WITH_MCP.md](docs/INSTALL_WITH_MCP.md): Notion 커넥터로 설치
- [DESIGN.md](DESIGN.md): 요청 디자인과 Notion 네이티브 표현 기준
- [DEFAULT_TEMPLATE.md](docs/DEFAULT_TEMPLATE.md): 네 페이지 배치·보기 탭·요일×교시 주간 시간표
- [DATA_MODEL.md](docs/DATA_MODEL.md): 관계와 데이터 입력 규칙
- [SCHOOL_CALENDAR.md](docs/SCHOOL_CALENDAR.md): NEIS 공개 학사일정 조회·중복 처리·주기 반영
- [MEALS.md](docs/MEALS.md): 홈 급식 조회·날짜 갱신·기존 수첩 추가
- [CLOUD_SYNC.md](docs/CLOUD_SYNC.md): 학교 재입력 없는 공통 클라우드 연결·매일 갱신
- [CLOUDFLARE.md](docs/CLOUDFLARE.md): 무료 플랜 시범 운영·사용 한도·운영자 배포
- [FIREBASE.md](docs/FIREBASE.md): Blaze를 사용하는 선택형 Firebase 배포
- [FORMS.md](docs/FORMS.md): 여섯 기록 양식 선택·복사·재사용
- [PARA.md](docs/PARA.md): 첨부 PARA를 교사 업무로 재설계한 근거
- [DAILY_USE.md](docs/DAILY_USE.md): 매일·매주 사용 및 AI 요청 예시
- [ITERATIVE_EDITING.md](docs/ITERATIVE_EDITING.md): 기존 수첩과 공통 템플릿을 AI와 함께 계속 수정하기
- [ACCEPTANCE.md](docs/ACCEPTANCE.md): 설치 확인 기준
- [RECOVERY.md](docs/RECOVERY.md): 중단 후 복구
- [SOURCES.md](docs/SOURCES.md): 검증한 공식 문서 및 참고 자료

실제 학생 기록과 토큰, 설치 ID는 Git에 올리지 않습니다. 출결은 개인 업무 보조 기록이며 공식 학적 시스템을 대체하도록 설계하지 않았습니다. Notion 하위 페이지나 필터는 권한 분리가 아니므로 실제 공유 권한은 별도로 확인합니다.

## 검증 상태

오프라인 테스트는 API 요청 구성, 설치 재실행, 관계 연결, 주간·월간 뷰, 실패 후 중복 방지, 시간표 변경·휴강, 컴시간·NEIS 응답 해석, 날짜·전체 페이지 검증, 급식 표시와 양식 재사용을 검증합니다. **2026-09-28에는 MCP로 개인 Notion에 예시 수첩을 생성하고, 원본 DB 16개와 가상 기록 53건의 속성·관계 및 빈 양식 6개를 실제로 다시 읽어 확인했습니다.** 개인 수첩 링크와 설치 기록은 공개하지 않습니다. 실제 학교의 시간표·학사일정·급식을 조회한 검증과는 구분합니다.

설치 환경마다 보기 설정이 다를 수 있으므로, 생성 성공 응답만으로 필터와 캘린더 범위까지 완료로 판단하지 않습니다. MCP는 [설치 안내](docs/INSTALL_WITH_MCP.md)의 실제 보기 재조회·UI 보완 절차를 따르고, Python 경로는 설치 후 `verify`를 실행하세요. 두 경로 모두 [화면 확인 기준](docs/ACCEPTANCE.md)의 실제 블록 재조회, 네 페이지 캡처, 페이지별 전체 너비 메뉴, 열 폭과 닫힌 접기를 확인합니다. 기능 확인과 배치 확인이 모두 통과해야 기본 템플릿 설치 완료이며, 증거가 부족하면 배치 확인 대기로 보고합니다.

```bash
python -m unittest discover -s tests -v
```

MIT License. 첨부받은 PARA와 교무수첩 원본 HTML·이미지·개인 기록은 포함하지 않으며, 새로 작성한 코드와 문서만 배포합니다.
