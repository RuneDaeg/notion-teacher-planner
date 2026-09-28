# 📒 나의 미니 교무수첩

**이 저장소를 자신의 생성형 AI에 주면, 교사에게 필요한 설정을 묻고 개인 Notion에 교무수첩을 만들도록 안내합니다.** 한국어 설명, 데이터베이스 설계, AI 실행 지침, Python 설치 도구를 함께 제공합니다.

기본 템플릿은 **대시보드·학급 경영·교과 진도·학사 일정**의 네 Notion 페이지로 구성됩니다. 오늘 필요한 기록을 대시보드에서 확인하고 각 업무 페이지로 이동합니다.

> 오늘의 수업과 꼭 해야 할 일을 한곳에. 작은 기록으로 가볍게 시작합니다.

## AI에게 이렇게 말하세요

```text
https://github.com/RuneDaeg/notion-teacher-planner 를 읽고
START_HERE.md 순서대로 내 Notion에 미니 교무수첩을 만들어줘.
학교급, 학년도와 학기, 담당 반과 교과, 추가 기능을 먼저 물어봐줘.
Notion 연결을 확인하고 내가 지정한 페이지 아래에 만들어줘.
```

AI가 GitHub를 읽을 수 없다면 저장소를 ZIP으로 내려받아 첨부하세요. 채팅 전용 AI는 설계 안내까지 가능하며, **실제 생성에는 Notion 쓰기 도구(MCP/커넥터) 또는 아래 Python 실행 환경과 Notion 연결 토큰이 필요합니다.** 도구가 없는데 생성했다고 답하지 않도록 실행 지침을 포함했습니다.

## 무엇이 들어가나요?

| 구성 | 사용 방법 |
| --- | --- |
| 오늘 급식 | NEIS 식단을 홈에 표시, 날짜·확인 시각·알레르기 번호 보존 |
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
📒 2026학년도 1학기 · 교무수첩 데스크 (대시보드)
 ├─ 학급 경영 & 학생 상담     명렬표·학생 카드·관찰·출결·상담·연락·제출
 ├─ 교과 진도표 & 시간표      주간 격자·변경 수업·반/교과/학기 진도·평가
 ├─ 학사 캘린더 & PARA        주간/월간/마감 탭·프로젝트·영역·자료·보관
 ├─ 양식 모음                선택한 기록 양식
 ├─ 운영 자료                업무 원본 DB
 └─ 학생 기록                학생 기록 원본 DB
```

모든 페이지에 같은 이동 링크가 있습니다. 할 일·학생·진도는 보기 탭으로 전환하고, **주간·월간 캘린더도 한 연결 DB 안의 탭**으로 사용합니다. 대시보드와 학사 캘린더 페이지의 연결 블록은 같은 업무·일정 원본을 공유합니다. 마감 탭은 같은 원본의 `마감`, 주간·월간은 `일정` 속성을 사용합니다.

교과 페이지의 주간 시간표와 홈의 `오늘의 수업`은 같은 교사 시간표 DB를 사용합니다. 파일·컴시간 가져오기는 시간표만 갱신하며, 업무·일정·To-Do나 학사 캘린더에 수업을 넣지 않습니다. 주간 격자는 빈 7교시와 공동 메모 행으로 시작합니다. 조회·종례 관찰 메모는 수동으로 작성합니다. 요약 숫자나 실시간 연동 표시를 임의로 채우지 않습니다. 공개 학사일정은 NEIS API에서 가져올 수 있습니다. 공식 NEIS 출결·학적 쓰기, 푸시 동기화·자동 메시지 발송은 제공하지 않습니다.

[디자인 기준](DESIGN.md)은 요청받은 색상·서체·화면 비율과 Notion 네이티브 구현 범위를 구분합니다. 정확한 1440px 화면·240px 사이드바·사용자 지정 CSS를 Notion에 고정하지 않습니다. 상세 배치와 교사별 설정은 [기본 템플릿 안내](docs/DEFAULT_TEMPLATE.md)를 참고하세요. 참고 이미지의 실제 이름·사진·전화번호·학교 링크는 기본값에 포함하지 않습니다.

## 화면 미리보기

아래는 네 페이지 구조를 보여 주는 설명용 빈 예시입니다. 실제 Notion 설치 화면이나 실데이터 캡처가 아니며, Notion의 서체·색상·열 너비는 실제 환경에서 달라질 수 있습니다.

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

`.local/config.json`에 학년도·교사·반·교과·추가 모듈을 입력합니다. 선택 항목 `semester`는 1 또는 2이며 생략하면 1학기로 표시합니다. 학교급은 설명용 메타데이터입니다. 담임이 아니면 `homeroom`을 `false`로 설정합니다. 시연용 가상 기록을 원할 때만 `demo`를 `true`로 바꿉니다.

선택적인 `dashboard` 설정으로 즐겨찾기·외부 자료 링크·표시 교시 수를 지정할 수 있습니다. 공유 페이지·설문·NEIS·에듀파인 주소를 선택적으로 입력합니다. 비어 있는 링크는 설정 안내로 표시합니다. 이 바로가기 설정은 페이지·설문 생성이나 자동 로그인·데이터 연동을 수행하지 않으며, 학사일정 연동은 아래 별도 명령으로 실행합니다. [설정 예시](docs/DEFAULT_TEMPLATE.md#교사별-설정)를 확인하세요.

```bash
# 토큰 없이 설계와 입력값 검토 — Notion에 쓰지 않습니다.
python -m teacher_planner plan --config .local/config.json
python -m teacher_planner install --config .local/config.json
```

[Notion 내부 연결](https://developers.notion.com/guides/get-started/create-a-notion-integration)을 만들고 읽기·삽입·수정 권한을 설정한 뒤, 대상 **일반 페이지**의 연결 메뉴에서 해당 연결을 추가합니다. 학생 기록을 쓸 경우 개인용 페이지에서 시작하세요. 토큰은 비밀 입력창 또는 로컬 환경 변수로만 제공합니다.

```bash
# 토큰이 이미 NOTION_TOKEN 환경 변수에 들어 있는 상태에서 실행
export NOTION_PARENT_PAGE_ID='대상_페이지의_URL_또는_UUID'
python -m teacher_planner install --config .local/config.json --apply
python -m teacher_planner verify
```

토큰을 입력할 때 셸 기록에 실제 값을 남기지 않는 예:

```bash
# bash/zsh; 입력 내용은 화면에 표시되지 않습니다.
printf 'Notion token: '
read -r -s NOTION_TOKEN
export NOTION_TOKEN
printf '\n'
```

`.env.example`은 변수 이름 안내이며 `.env`를 자동 실행하거나 읽지 않습니다. 설치 상태와 ID는 `.local/state.json`에만 저장됩니다. 정상 설치 재실행은 생성한 객체를 재사용합니다. 설정이나 학년도를 바꾸어 새 수첩을 만들 때는 `--state .local/2027-state.json`처럼 별도 상태 경로를 지정합니다. 기존 학년도 자료는 자동 변경하지 않습니다.

새 기본 템플릿은 새 설치에 적용합니다. 이전 버전의 상태로 `install`을 실행하면 설계 변경을 감지해 중단하며 기존 페이지를 덮어쓰지 않습니다. 새 배치로 시작하려면 별도 상태 경로로 새 수첩을 만드세요. 기존 수첩의 시간표 가져오기·동기화는 기존 경로를 사용할 수 있지만 새 페이지 배치가 자동 추가되지는 않습니다. 자료 이동은 필요한 범위를 정해 별도로 진행합니다.

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

학교 코드·교사 번호는 `COMCIGAN_SCHOOL_CODE`, `COMCIGAN_TEACHER_ID` 환경 변수로 대신 제공할 수 있습니다. `--date YYYY-MM-DD`로 조회할 주를 지정하며, 생략하면 서울 기준 이번 주입니다. Notion 반영에는 기존 설치 상태·반·교과 설정이 필요합니다. 교시 시각을 설정하면 시간표의 수업일에 정확한 시각을 기록하고, 생략하면 날짜만 기록합니다. [설정과 동기화 사용법](docs/COMCIGAN.md)을 먼저 확인하세요. 이전 버전에서 업무·일정에 복사했던 수업은 자동 삭제하지 않으며, 필요한 정리는 대상 기록을 확인한 별도 작업으로 진행합니다.

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

같은 학교·과정·날짜·행사의 중복을 막고 설명 변경을 갱신합니다. 날짜나 행사명이 바뀌면 새 일정이 생기며 이전 일정은 남습니다. 누락된 행사를 자동 삭제·취소하지 않습니다. `--watch --interval 21600 --apply`로 실행 중인 프로세스에서 6시간마다 조회할 수 있습니다. [인증키·기간·반영 규칙](docs/SCHOOL_CALENDAR.md)을 확인하세요.

## 오늘 급식과 기록 양식

홈의 급식 영역에 NEIS 공개 식단을 표시할 수 있습니다. 학사일정과 같은 `NEIS_API_KEY`, 시도교육청 코드·표준학교코드를 사용하며 업무·일정이나 시간표에 행을 만들지 않습니다.

```bash
# 기존 수첩에 급식 영역과 선택한 양식 모음 추가
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json --apply

# 오늘 식단 조회 후 홈에 반영
python -m teacher_planner meals-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --apply
```

새 설치에는 급식 안내 영역과 현재 설정에 맞는 양식이 포함됩니다. `setup-extras`는 기존 수첩의 페이지·DB·기록을 다시 만들지 않습니다. 급식 조회는 별도로 실행하며 `--watch --interval 21600 --apply`로 실행 중 6시간마다 갱신하고 날짜가 바뀌면 새 날짜를 조회합니다. 프로세스가 멈추면 마지막 날짜의 표시가 남습니다. [급식 설정](docs/MEALS.md)을 확인하세요.

`양식 모음`은 상담·학부모 연락·회의록·수업·평가·조회/종례의 **복사용 빈 페이지 양식**입니다. DB의 새로 만들기 템플릿으로 자동 등록되지는 않습니다. `forms`로 선택하고 양식 본문을 새 기록에 복사합니다. [설정과 사용법](docs/FORMS.md)을 참고하세요.

## AI와 개발자를 위한 안내

- [START_HERE.md](START_HERE.md): AI가 읽을 첫 문서
- [AGENTS.md](AGENTS.md): 실행 범위와 데이터 취급 원칙
- [INSTALL_WITH_MCP.md](docs/INSTALL_WITH_MCP.md): Notion 커넥터로 설치
- [DESIGN.md](DESIGN.md): 요청 디자인과 Notion 네이티브 표현 기준
- [DEFAULT_TEMPLATE.md](docs/DEFAULT_TEMPLATE.md): 네 페이지 배치·보기 탭·시간표 격자
- [DATA_MODEL.md](docs/DATA_MODEL.md): 관계와 데이터 입력 규칙
- [SCHOOL_CALENDAR.md](docs/SCHOOL_CALENDAR.md): NEIS 공개 학사일정 조회·중복 처리·주기 반영
- [MEALS.md](docs/MEALS.md): 홈 급식 조회·날짜 갱신·기존 수첩 추가
- [FORMS.md](docs/FORMS.md): 여섯 기록 양식 선택·복사·재사용
- [PARA.md](docs/PARA.md): 첨부 PARA를 교사 업무로 재설계한 근거
- [DAILY_USE.md](docs/DAILY_USE.md): 매일·매주 사용 및 AI 요청 예시
- [ACCEPTANCE.md](docs/ACCEPTANCE.md): 설치 확인 기준
- [RECOVERY.md](docs/RECOVERY.md): 중단 후 복구
- [SOURCES.md](docs/SOURCES.md): 검증한 공식 문서 및 참고 자료

실제 학생 기록과 토큰, 설치 ID는 Git에 올리지 않습니다. 출결은 개인 업무 보조 기록이며 공식 학적 시스템을 대체하도록 설계하지 않았습니다. Notion 하위 페이지나 필터는 권한 분리가 아니므로 실제 공유 권한은 별도로 확인합니다.

## 검증 상태

오프라인 테스트는 API 요청 구성, 설치 재실행, 관계 연결, 주간·월간 뷰, 실패 후 중복 방지, 시간표 변경·휴강, 컴시간·NEIS 응답 해석, 날짜·전체 페이지 검증, 급식 표시와 양식 재사용을 검증합니다. **이 저장소 제작 과정에서는 사용자 요청에 따라 실제 Notion에 생성하지 않았으며, 실제 학교의 시간표·학사일정·급식을 조회해 검증하지 않았습니다.** 실제 연결에서는 설치 후 `verify`와 [화면 확인 기준](docs/ACCEPTANCE.md)을 수행하세요.

```bash
python -m unittest discover -s tests -v
```

MIT License. 첨부받은 PARA와 교무수첩 원본 HTML·이미지·개인 기록은 포함하지 않으며, 새로 작성한 코드와 문서만 배포합니다.
