# Notion MCP/커넥터로 설치하기

이 경로는 별도 토큰을 채팅에 전달하지 않고 AI에 연결된 Notion 도구로 생성합니다. 도구가 읽기 전용이면 쓰기 경로를 안내하고 멈춥니다. 도구 명세에 접근 가능한 기능·요금제 제한이 있으면 먼저 확인합니다.

## 준비

1. `START_HERE.md`의 질문 답을 받는다. 설정을 `.local/config.json` 또는 사용자와 합의한 비공개 기록에 저장한다.
2. 연결된 계정/워크스페이스를 확인한다. 사용자가 준 상위 페이지를 fetch하여 일반 페이지인지 확인한다.
3. Notion의 enhanced Markdown 명세와 view DSL 명세를 도구로 읽는다. 도구 이름은 하드코딩하지 않는다.
4. 상위 페이지 자식과 기존 설치 기록을 확인한다. 같은 수첩이 있으면 재사용 가능한지 검사한다. 제목만 같은 무관한 DB는 연결하지 않는다.

## 설계 원본

`teacher_planner/blueprint.json`이 속성과 뷰의 기준이다. 로컬 실행이 가능하면 아래 명령으로 선택 모듈을 반영한 전체 명세를 얻는다.

```bash
python -m teacher_planner plan --config .local/config.json --full
```

DB를 만들 때 `key → database_id → data_source_id` 매핑을 저장한다. MCP 설치 기록은 `.local/mcp-install.json`에 보관하고 Python state와 섞지 않는다. 로컬 저장이 없는 클라이언트에서는 사용자가 관리하는 설치 메모에 URL을 기록한다.

## 생성 순서

1. 상위 페이지 아래 `학년도 · 나의 미니 교무수첩`과 `운영 자료`, `학생 기록` 하위 페이지를 만든다. 모든 DB와 홈 뷰는 이 수첩 안에 둔다.
2. 선택된 DB들의 기본 속성을 만든다. SQL DDL 기반 도구라면 `title → TITLE`, `rich_text → RICH_TEXT`, `number → NUMBER`, `date → DATE`, `checkbox → CHECKBOX`, `url → URL`, `select → SELECT('선택1', '선택2')`, `multi_select → MULTI_SELECT(...)`, `formula → FORMULA('표현식')`로 변환한다. 실제 도구의 이스케이프 규칙을 적용한다.
3. 관계 속성은 모든 DB 생성 후 `RELATION('실제 data_source_id')`로 추가한다. 원본 정의는 한 방향 관계이다. 임의로 양방향 관계를 추가해 속성 수를 늘리지 않는다.
4. `담당 교과` 선택지를 실제 교과로 맞추고 학급·담당 영역을 넣는다. 모든 행에 학년도를 넣는다. 데모 요청이 없으면 학생·상담 예시는 넣지 않는다.
5. 뷰를 생성한다. 원본 DB에 뷰 탭을 만들고, `dashboard_views` 6개를 홈 페이지에 linked view로 만든다.
6. 모든 현재 학년도 뷰에는 `학년도 = 설정값`, `보관 = false`를 적용한다. 보관함은 `학년도 = 설정값`, `보관 = true`다. 기본 생성 뷰가 추가로 존재해도 괜찮다.

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
