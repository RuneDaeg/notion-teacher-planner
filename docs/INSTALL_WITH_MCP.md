# Notion MCP/커넥터로 설치하기

이 경로는 별도 토큰을 채팅에 전달하지 않고 AI에 연결된 Notion 도구로 생성합니다. 도구가 읽기 전용이면 쓰기 경로를 안내하고 멈춥니다. 도구 명세에 접근 가능한 기능·요금제 제한이 있으면 먼저 확인합니다.

## 준비

1. `START_HERE.md`의 질문 답을 받는다. 설정을 `.local/config.json` 또는 사용자와 합의한 비공개 기록에 저장한다.
2. 연결된 계정/워크스페이스를 확인한다. 사용자가 준 상위 페이지를 fetch하여 일반 페이지인지 확인한다.
3. Notion의 enhanced Markdown 명세와 view DSL 명세를 도구로 읽는다. 도구 이름은 하드코딩하지 않는다.
4. 상위 페이지 자식과 기존 설치 기록을 확인한다. 같은 수첩이 있으면 재사용 가능한지 검사한다. 제목만 같은 무관한 DB는 연결하지 않는다.

## 설계 원본

`teacher_planner/blueprint.json`이 속성과 뷰의 기준이고, `teacher_planner/dashboard.json`이 기본 홈 배치의 기준이다. [DEFAULT_TEMPLATE.md](DEFAULT_TEMPLATE.md)의 영역·순서·링크 규칙을 함께 따른다. 로컬 실행이 가능하면 아래 명령으로 선택 모듈과 홈 구성을 반영한 전체 명세를 얻는다.

```bash
python -m teacher_planner plan --config .local/config.json --full
```

DB를 만들 때 `key → database_id → data_source_id` 매핑을 저장한다. MCP 설치 기록은 `.local/mcp-install.json`에 보관하고 Python state와 섞지 않는다. 로컬 저장이 없는 클라이언트에서는 사용자가 관리하는 설치 메모에 URL을 기록한다.

## 생성 순서

1. 상위 페이지 아래 `학년도 · 나의 미니 교무수첩`과 `운영 자료`, `학생 기록` 하위 페이지를 만든다. 모든 DB와 홈 뷰는 이 수첩 안에 둔다.
2. 선택된 DB들의 기본 속성을 만든다. SQL DDL 기반 도구라면 `title → TITLE`, `rich_text → RICH_TEXT`, `number → NUMBER`, `date → DATE`, `checkbox → CHECKBOX`, `url → URL`, `select → SELECT('선택1', '선택2')`, `multi_select → MULTI_SELECT(...)`, `formula → FORMULA('표현식')`로 변환한다. 실제 도구의 이스케이프 규칙을 적용한다.
3. 관계 속성은 모든 DB 생성 후 `RELATION('실제 data_source_id')`로 추가한다. 원본 정의는 한 방향 관계이다. 임의로 양방향 관계를 추가해 속성 수를 늘리지 않는다.
4. `담당 교과` 선택지를 실제 교과로 맞추고 학급·담당 영역을 넣는다. 모든 행에 학년도를 넣는다. 데모 요청이 없으면 학생·상담 예시는 넣지 않는다.
5. 원본 DB의 뷰 탭을 만든다. 홈에는 `blueprint.json`의 `dashboard_views` 중 선택 모듈에 맞는 연결 뷰만 `dashboard.json`의 해당 영역에 배치한다. `shared_view_groups`의 `weekly`·`monthly`는 한 홈 연결 DB의 보기 탭으로 만든다. 모든 선택 기능을 켜면 홈 뷰 8개·연결 DB 블록 7개다.
6. 모든 현재 학년도 뷰에는 `학년도 = 설정값`, `보관 = false`를 적용한다. 보관함은 `학년도 = 설정값`, `보관 = true`다. 기본 생성 뷰가 추가로 존재해도 괜찮다.

교직원 연락망을 선택했다면 `교내연락처`와 `이메일`을 각각 전화번호·이메일 유형으로 만든다. SQL DDL 도구를 사용할 때는 해당 도구의 실제 유형 명세로 변환한다. 수업 진도의 `학기`는 `1학기 / 2학기` 선택이며, 학기별 진도는 같은 DB의 필터 뷰로 만든다.

## 기본 홈 배치

- 상단은 약 62.5% / 37.5%의 2열이다. 왼쪽에 `교시·수업 시간·월~금` 시간표 표, 오른쪽에 빠른 동작과 즐겨찾기를 둔다. 첫 표에는 실제 수업·시각을 추정해 채우지 않는다.
- 이어서 `Things to do`와 선택한 경우의 `회의록`을 전폭으로 배치한다.
- 가운데는 약 21% / 58% / 21%의 3열이다. 왼쪽에 `Class / Share / Advice`, 가운데에 `학생 명렬표`, 오른쪽에 `School / ETC`를 둔다.
- 아래에는 실제 학생 연결 뷰, `Schedule`의 주간·월간 캘린더 탭을 가진 연결 DB 하나와 별도 상세 교사 주간 시간표, `Archive`를 둔다. 각 바로가기는 이 설치에서 만든 DB·페이지의 ID를 사용한다.

REST 뷰 생성의 `create_database.parent`는 `page_id`를 사용한다. Python 설치는 이 범위에 맞춰 실제 학생 연결 뷰를 3열 아래 전폭으로 배치한다. **MCP의 실제 명세에서 열 내부 연결 뷰 배치를 지원할 때만** 학생 연결 뷰를 가운데 열에 둔다. 지원하지 않는 `block_id`·뷰 이동 파라미터를 만들어 호출하지 않는다. 제목과 링크만 놓았다면 그것이 실제 표인 것처럼 보고하지 않는다.

빠른 동작은 생성된 시간표·진도·업무·자료로 이동하는 링크다. 자동화 실행 버튼으로 가장하지 않는다. 외부 즐겨찾기와 `Share`의 공유 페이지·설문은 사용자 설정이 있을 때만 연결한다. 미설정 항목에는 설정 안내를 남기고, 원본 학교 링크나 `#` 같은 가짜 링크를 넣지 않는다. 교직원 연락망·학교 계정 DB는 선택한 경우에만 생성하고 `School` 메뉴에 연결한다. 계정 DB에는 비밀번호·복구 코드·토큰 필드를 추가하지 않는다. 설문 자체의 제작이나 공개 공유는 별도 요청 범위다.

원본의 손글씨 이미지 대신 네이티브 제목·색상·구분선을 사용한다. 참고 HTML의 실제 시간표·회의 기록·학교 및 Notion 링크를 복사하지 않는다. 상단 시간표는 표시용 표이며 시간표 DB가 원본이다. MCP로 만든 표를 Python 설치 상태와 연결하지 않은 경우, Python 명령이 그 표까지 자동 갱신한다고 안내하지 않는다. MCP 설치 담당자가 검증한 시간표 DB 자료로 해당 주의 표를 갱신하고 주간 날짜를 표시한다.

## 한 캘린더에 주간·월간 탭 만들기

MCP에서 기존 연결 DB에 뷰를 추가하는 실제 도구 명세를 확인한다. 이름은 `주간 캘린더`와 `월간 캘린더`이며, 두 뷰 모두 업무·일정 원본의 같은 `일정` 날짜 속성을 사용한다. REST를 사용하는 경우 순서는 다음과 같다.

1. `data_source_id`를 업무·일정 원본 ID로 지정하고 `create_database.parent.page_id`를 홈으로 지정해 주간 뷰를 만든다. 표시 범위는 `configuration.view_range: week`다.
2. 성공 응답의 `parent.database_id`를 홈 캘린더 연결 DB의 ID로 저장한다. 원본 DB의 ID 또는 주간 view ID와 구분한다.
3. 월간 뷰는 저장한 홈 캘린더 `database_id`와 **같은 `data_source_id`**로 생성한다. 이 요청에는 `create_database`를 넣지 않는다. 날짜 속성 ID는 그대로 두고 표시 범위는 `month`로 설정한다.
4. 두 보기의 부모 database ID·data source ID·날짜 속성 ID가 각각 같은지 확인한다. 원격 홈을 열어 한 캘린더의 두 탭으로 전환되는지 확인한다.

`teacher_week`는 교사 시간표 원본을 보여 주므로 별도의 연결 DB로 만든다. 월간 탭 생성 중 실패하면 저장한 주간 캘린더의 부모 DB를 재사용하고 [RECOVERY.md](RECOVERY.md)에 따라 이어간다. 기존 홈 블록을 삭제하거나 월간용 연결 DB를 새로 만드는 것으로 실패를 숨기지 않는다.

예: 실제 도구가 Notion view DSL을 지원할 경우 다음 설정을 쓸 수 있다.

```text
# 우선순위 To Do (table)
FILTER "학년도" = 2026; FILTER "보관" = FALSE
FILTER "상태" != "완료" AND "상태" != "취소"
SORT BY "우선순위 순서" ASC, "마감" ASC
SHOW "이름", "우선순위", "마감", "업무 분류", "상태", "다음 행동"

# 캘린더 (calendar)
CALENDAR BY "일정"
FILTER "학년도" = 2026; FILTER "보관" = FALSE; FILTER "상태" != "취소"

# 업무 분류별 (board)
GROUP BY "업무 분류"
```

`2026`은 교사의 실제 학년도로 바꾼다. 주간·월간은 이름만 바꾸면 안 된다. 현재 MCP DSL에는 범위 지시어가 없을 수 있다. 해당 도구에서 주/월 범위를 지원하지 않으면 Notion UI의 **뷰 설정 → 레이아웃 → 캘린더 표시 방식 → 주/월**을 설정하거나 REST Views API의 `configuration.view_range`를 쓴다. 지원 여부를 확인하지 않고 `WEEK` 같은 DSL을 만들지 않는다.

REST 설정 구조는 `model.view_payload()`에 있다. 속성 ID는 data source fetch 결과에서 가져온다. 날짜는 `date_property_id`, 범위는 `view_range: week/month`, 그룹은 `group_by`를 쓴다.

## 불완전한 설치

권한 부족, 뷰 API 미지원, 응답 유실 시 이미 만든 객체 URL을 보존한다. 후속 실행에서 기존 객체를 fetch한 뒤 이어간다. 기존 DB를 지웠다가 다시 만들지 않는다. 최종 답변에 생성 완료 항목과 수동으로 남은 항목을 나누어 명시한다.
