# 홈의 오늘 급식

NEIS 교육정보 개방 포털의 급식식단정보를 조회해 홈의 급식 콜아웃에 표시한다. 학교·기준 날짜·마지막 확인 시각·출처와 제공된 조식·중식·석식을 함께 보여 준다. 급식 내용을 업무·일정·시간표·To-Do의 행으로 만들지 않는다.

새 설치에는 급식 안내 영역이 생기며 실제 조회는 사용자가 요청한 경우 별도로 실행한다. 기존 수첩에는 `setup-extras`로 영역을 추가한다. 초기 안내나 조회되지 않은 상태를 실제 식단처럼 소개하지 않는다.

## 필요한 값

학사일정과 같은 NEIS 인증키·학교 식별값을 사용한다. `NEIS_API_KEY`는 로컬 환경 변수로만 제공한다. 시도교육청 코드와 표준학교코드는 `--office-code`, `--school-code` 또는 `NEIS_OFFICE_CODE`, `NEIS_SCHOOL_CODE`로 지정한다. 컴시간 학교 코드는 사용하지 않는다.

공식 API의 키 없는 샘플은 5건으로 제한되므로 발급받은 인증키가 필요하다. 메뉴·원산지·칼로리·영양정보와 메뉴의 알레르기 번호를 제공한다. [NEIS 급식식단정보 안내](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17320190722180924242823&infSeq=2)

인증키 입력과 환경 변수 설정은 [학사일정 안내](SCHOOL_CALENDAR.md#필요한-값)를 따른다. 키·요청 URL·설치 상태를 Git이나 채팅에 공개하지 않는다. `.env.example`은 이름 안내이며 `.env` 파일을 자동으로 읽지 않는다.

## 조회만 하기

```bash
python -m teacher_planner neis-meals --office-code 시도교육청코드 --school-code 표준학교코드 --output .local/neis-meals.json

# 다른 날짜를 확인할 때
python -m teacher_planner neis-meals --office-code 시도교육청코드 --school-code 표준학교코드 --date 2026-09-28 --output .local/neis-meals.json
```

`--date`를 생략하면 Asia/Seoul 기준 오늘 날짜다. 기본 출력은 `.local/neis-meals.json`이며 `.local/` 또는 `private/` 아래에 저장한다. 이 명령은 Notion에 접속하거나 쓰지 않는다. 요청한 학교·날짜와 조회 결과를 먼저 확인한다.

## 기존 홈에 표시하기

기존 수첩에 아직 급식 영역이 없다면 먼저 추가한다. `setup-extras`는 급식 영역과 설정에 맞는 [양식 모음](FORMS.md)을 추가하며 기존 네 페이지·DB·수동 기록을 다시 만들지 않는다.

```bash
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json
python -m teacher_planner setup-extras --config .local/config.json --state .local/state.json --apply

# 식단 조회·검토만 실행
python -m teacher_planner meals-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --state .local/state.json

# 기존 홈 급식 영역에 반영
python -m teacher_planner meals-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --state .local/state.json --apply
```

`--apply`에는 설치 당시의 `NOTION_TOKEN`, 완료된 Python 설치 상태와 같은 config가 필요하다. `--apply` 없는 명령은 식단 조회만 하며 Notion 블록의 차이를 비교하지 않는다. `meals-sync`에도 일회성 조회·반영에는 `--date YYYY-MM-DD`를 지정할 수 있다. Notion에 반영할 날짜는 수첩 학년도 3월 1일~다음 해 2월 말 안이어야 한다. 과거 날짜를 반영한 경우 표시된 날짜를 확인한다. MCP 설치 ID를 Python 상태로 임의 변환하지 않는다.

기존 NEIS 학사일정 또는 급식 연결이 있으면 교육청·학교·수첩 학년도가 일치해야 한다. 다른 학교를 표시하려고 연결 상태를 지우지 않는다.

메뉴의 알레르기 번호는 원문대로 보존한다. 칼로리·원산지·영양정보는 제공된 경우 표시하고 비어 있는 값을 추정하지 않는다. 급식 영역은 연동이 갱신하는 표시 공간이므로 개인 메모는 다른 블록에 쓴다.

정상 조회에서 자료가 없으면 **‘공개된 급식 정보 없음’**으로 표시한다. 이를 급식이 제공되지 않는다는 확정 정보로 해석하지 않는다. 인증·네트워크·날짜·학교 검증 실패는 자료 없음과 구분하며 이전 표시를 유지한다.

## 주기 갱신과 날짜 변경

```bash
python -m teacher_planner meals-sync --office-code 시도교육청코드 --school-code 표준학교코드 --config .local/config.json --state .local/state.json --apply --watch --interval 21600
```

`--watch`는 `--apply`와 함께만 사용하고 `--date`와 함께 사용할 수 없다. 매 조회마다 서울 기준 오늘을 다시 계산한다. 설정 간격은 기본 21,600초(6시간), 최소 3,600초(1시간)이다. 실행 중에는 다음 간격과 서울 자정 중 먼저 도래하는 시점에 다시 조회한다. 새 날짜가 수첩 학년도를 벗어나면 반영을 중단하며 새 학년도 수첩으로 자동 전환하지 않는다.

프로세스가 실행되는 동안만 동작한다. PC 종료·절전·프로세스 종료 중에는 **마지막으로 반영한 날짜와 결과가 그대로 남는다.** 홈에서 기준 날짜와 확인 시각을 확인한다. 자동 예약 작업·서버 설치나 실시간 푸시를 제공하지 않는다. 오류로 조회가 실패하면 기존 표시를 남기고 원인을 확인한 뒤 재실행한다.

## 실패와 검증

급식 블록 생성 중 응답이 유실되면 상태 파일을 보존하고 [RECOVERY.md](RECOVERY.md)를 따른다. 기존 블록을 찾지 못한다고 새 수첩을 만들거나 홈 전체를 교체하지 않는다.

실제 학교 조회와 실제 Notion 표시를 각각 확인한다. 오프라인 모의 테스트만으로 실제 학교의 식단 표시를 검증했다고 보고하지 않는다. 상세 검증은 [급식 확인 기준](ACCEPTANCE.md#홈-급식을-선택한-경우)을 따른다.
