# 📒 나의 미니 교무수첩

**이 저장소를 자신의 생성형 AI에 주면, 교사에게 필요한 설정을 묻고 개인 Notion에 교무수첩을 만들도록 안내합니다.** 한국어 설명, 데이터베이스 설계, AI 실행 지침, Python 설치 도구를 함께 제공합니다.

> 오늘의 수업과 꼭 해야 할 일을 한곳에. 작은 기록으로 가볍게 시작합니다.

## AI에게 이렇게 말하세요

```text
https://github.com/RuneDaeg/notion-teacher-planner 를 읽고
START_HERE.md 순서대로 내 Notion에 미니 교무수첩을 만들어줘.
학교급, 학년도, 담당 반과 교과, 추가 기능을 먼저 물어봐줘.
Notion 연결을 확인하고 내가 지정한 페이지 아래에 만들어줘.
```

AI가 GitHub를 읽을 수 없다면 저장소를 ZIP으로 내려받아 첨부하세요. 채팅 전용 AI는 설계 안내까지 가능하며, **실제 생성에는 Notion 쓰기 도구(MCP/커넥터) 또는 아래 Python 실행 환경과 Notion 연결 토큰이 필요합니다.** 도구가 없는데 생성했다고 답하지 않도록 실행 지침을 포함했습니다.

## 무엇이 들어가나요?

| 구성 | 사용 방법 |
| --- | --- |
| 학생 명단 | 학급·번호·학생 ID로 명단 관리 |
| 상담 기록 | 학생 연결, 관찰 사실, 합의 사항, 후속 확인 |
| To Do List | P1~P4, 업무 분류, 상태, 다음 행동, 마감 |
| 주간·월간 캘린더 | 같은 업무·일정 원본의 두 가지 화면 + 마감 캘린더 |
| 교사 시간표 | 날짜·교시·교과·반, CSV/JSON 가져오기, 선택형 컴시간 웹 조회·동기화 |
| 수업 진도 | 교과별·반별 단원, 계획/완료 차시, 진도율, 다음 수업 |
| 교사용 PARA | 프로젝트, 담당 영역, 자료, 보관 + 수집함 |
| 선택: 출결·제출물 | 학생별 출결과 제출 상태 체크 |
| 선택: 평가·채점 | 평가 일정, 채점 마감, 채점 완료 인원·진행률 |
| 선택: 학부모 연락 | 연락 요지, 합의 사항, 후속 업무 |
| 선택: 회의록 | 안건, 결정, 담당, 후속 업무 |

기본은 9개, 추가 기능을 모두 선택하면 **14개 데이터베이스·45개 뷰(홈 연결 뷰 6개 포함)**입니다. 이 저장소 제작 요청에서는 추가 기능을 모두 선택했지만, 재사용할 때는 각 교사에게 다시 묻습니다. 예시 설정은 모든 모듈이 켜져 있습니다.

```text
📒 2026 · 나의 미니 교무수첩
 ├─ 운영 자료      학급 / PARA / 업무·일정 / 시간표 / 진도 / 평가 / 회의
 ├─ 학생 기록      학생 명단 / 상담 / 출결 / 제출물 / 학부모 연락
 ├─ 수집함         일단 빠르게 적기
 ├─ 우선순위 To Do 지금 중요한 일부터
 ├─ 주간 캘린더    이번 주 수업과 약속
 ├─ 월간 캘린더    한 달 일정 확인
 ├─ 교사 주간 시간표
 └─ 반별 진도
```

## Python으로 설치하기

Python 3.10 이상. 외부 런타임 패키지 없이 저장소 폴더에서 실행합니다. Windows에서 IANA 시간대가 없다면 `python -m pip install tzdata`가 필요할 수 있습니다.

```bash
git clone https://github.com/RuneDaeg/notion-teacher-planner.git
cd notion-teacher-planner
mkdir -p .local
cp config.example.json .local/config.json
```

`.local/config.json`에 학년도·교사·반·교과·추가 모듈을 입력합니다. 학교급은 설명용 메타데이터입니다. 담임이 아니면 `homeroom`을 `false`로 설정합니다. 시연용 가상 기록을 원할 때만 `demo`를 `true`로 바꿉니다.

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

학교 코드·교사 번호는 `COMCIGAN_SCHOOL_CODE`, `COMCIGAN_TEACHER_ID` 환경 변수로 대신 제공할 수 있습니다. `--date YYYY-MM-DD`로 조회할 주를 지정하며, 생략하면 서울 기준 이번 주입니다. Notion 반영에는 기존 설치 상태·반·교과 설정이 필요합니다. 교시 시각을 설정하면 캘린더에 정확한 시각으로, 생략하면 날짜만 표시합니다. [설정과 동기화 사용법](docs/COMCIGAN.md)을 먼저 확인하세요.

이 기능은 **공식 API가 아닌 선택형 웹 연동**입니다. `--watch --interval 600 --apply`로 실행 중인 프로세스에서 10분마다 갱신할 수 있으며, 자동 예약 작업은 설치하지 않습니다. 웹 구조 변경·접근 제한·해당 주 미제공 시 오류로 중단하며 로그인이나 접근 제한을 우회하지 않습니다.

## AI와 개발자를 위한 안내

- [START_HERE.md](START_HERE.md): AI가 읽을 첫 문서
- [AGENTS.md](AGENTS.md): 실행 범위와 데이터 취급 원칙
- [INSTALL_WITH_MCP.md](docs/INSTALL_WITH_MCP.md): Notion 커넥터로 설치
- [DATA_MODEL.md](docs/DATA_MODEL.md): 관계와 데이터 입력 규칙
- [PARA.md](docs/PARA.md): 첨부 PARA를 교사 업무로 재설계한 근거
- [DAILY_USE.md](docs/DAILY_USE.md): 매일·매주 사용 및 AI 요청 예시
- [ACCEPTANCE.md](docs/ACCEPTANCE.md): 설치 확인 기준
- [RECOVERY.md](docs/RECOVERY.md): 중단 후 복구
- [SOURCES.md](docs/SOURCES.md): 검증한 공식 문서 및 참고 자료

실제 학생 기록과 토큰, 설치 ID는 Git에 올리지 않습니다. 출결은 개인 업무 보조 기록이며 공식 학적 시스템을 대체하도록 설계하지 않았습니다. Notion 하위 페이지나 필터는 권한 분리가 아니므로 실제 공유 권한은 별도로 확인합니다.

## 검증 상태

오프라인 테스트는 API 요청 구성, 설치 재실행, 관계 연결, 주간·월간 뷰, 실패 후 중복 방지, 시간표 변경·휴강, 컴시간 응답 해석·날짜 검증을 검증합니다. **이 저장소 제작 과정에서는 사용자 요청에 따라 실제 Notion에 생성하지 않았으며, 실제 학교 코드·교사 번호로 시간표를 조회해 검증하지 않았습니다.** 실제 연결에서는 설치 후 `verify`와 [화면 확인 기준](docs/ACCEPTANCE.md)을 수행하세요.

```bash
python -m unittest discover -s tests -v
```

MIT License. 첨부받은 PARA 원본 HTML·브랜드 자산·개인 기록은 포함하지 않으며, 새로 작성한 코드와 문서만 배포합니다.
