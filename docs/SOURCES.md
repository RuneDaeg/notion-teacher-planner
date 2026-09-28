# 근거와 지원 범위

확인일: 2026-09-26~28. 아래는 구현 시 확인한 1차 자료다. API/제품은 바뀔 수 있으므로 버전을 올릴 때 다시 확인한다.

- [Notion 데이터베이스 생성](https://developers.notion.com/reference/create-a-database): `initial_data_source.properties`로 스키마 생성.
- [Notion 뷰 생성](https://developers.notion.com/reference/create-view): 기존 DB의 뷰 또는 페이지의 linked database view 생성.
- [Notion 뷰 구성](https://developers.notion.com/guides/data-apis/working-with-views): 캘린더 `date_property_id`, `view_range`의 week/month, 그룹 설정, 연결 뷰의 부모 구조.
- [Notion 블록](https://developers.notion.com/reference/block): 제목·구분선·표·열 구성. `column.width_ratio`의 합은 1이며 열 묶음을 생성할 때는 최소 두 열과 각 열의 자식 블록이 필요함. 네 페이지는 원본 이미지를 임베드하지 않고 이 블록들로 구성.
- [Notion 수식 문법과 함수](https://www.notion.com/help/formula-syntax): `today`, `dateStart`, `formatDate`, `parseDate`, `dateBetween`, `empty`와 조건 함수의 문법. 오늘 보기·달력 날짜 기준 D-Day 수식에 사용하며, 실제 워크스페이스에서의 수식 실행 검증과 문법 확인은 구분한다.
- [Notion 내부 연결](https://developers.notion.com/guides/get-started/create-a-notion-integration): 토큰과 페이지 접근 설정.
- [NEIS 급식식단정보 API](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17320190722180924242823&infSeq=2): 일자별 메뉴·원산지·칼로리·영양정보, 메뉴에 붙은 알레르기 번호를 제공하며 적재 주기는 매일로 안내됨. 인증키 없는 5건 샘플과 실제 학교 조회를 구분함. 홈 표시의 기준 날짜·확인 시각은 실제 조회 결과를 사용함.
- [NEIS 공개 학사일정 API](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2): 학교별 행사 날짜·이름·내용·대상 학년을 제공하며 적재 주기는 매일로 안내됨. 인증키 없는 샘플은 1페이지·5건으로 제한되므로 실제 조회에는 API 키와 전체 페이지 확인을 사용함. 공개 행사 조회이며 공식 출결·학적 쓰기의 근거로 사용하지 않음.
- [컴시간 공식 질의응답: 파일 연동 문의](https://comcigan.co.kr/xe/FAQ/89859): 엑셀 출력에 대한 운영자 답변. 학원시간표 문의이므로 모든 학교 제품의 동일 지원을 보장하는 근거로 쓰지 않음.
- [컴시간 교사 웹 진입점](http://comci.kr/th/), [공개 교사 웹 클라이언트](http://comci.net:4082/th): 공개 클라이언트의 학교·교사 선택, 주별 자료 요청, 날짜·열람 제한·시간표 응답 해석을 확인. 호출 경로와 응답 필드는 변경될 수 있어 동적으로 확인하고, 지원하지 않는 형식에서는 중단한다. 공식 외부 API 계약으로 해석하지 않음. 현재 HTTP로 제공되며 인증 우회는 구현하지 않음.
- 사용자 제공 `PARA of LIGHT` HTML: Inbox/Projects/Areas/Resources/Archives와 Tasks/Notes/Topics 구조를 참고. 원본은 저장소에 배포하지 않음. 교사용 변환 내용은 [PARA.md](PARA.md)에 기록.
- 사용자 제공 `노션 교무수첩 기본 템플릿` HTML과 동봉 파일: 이전 기본 배치에서 시간표·바로가기·명렬표·자료 메뉴·Schedule·Archive 구조를 참고. 버전 3은 후속 요청의 네 페이지 구조로 재편함. 원본 HTML·PNG·CSV, 실제 시간표·회의 기록·학교 URL·Notion 식별값은 배포하지 않음.
- 사용자 제공 DESIGN.md 본문과 네 화면 참고 이미지: 대시보드, 학급 경영·상담, 교과 진도·시간표, 학사 캘린더·PARA의 구조와 따뜻한 회색 계열 디자인 토큰을 참고. 사용자가 확인한 구현 대상은 기존 Notion 내부 템플릿이다. 이미지 속 개인정보나 구현되지 않은 연동·집계를 복사하지 않음. 참고값은 [DESIGN.md](../DESIGN.md), 실제 네이티브 구현 범위는 [DEFAULT_TEMPLATE.md](DEFAULT_TEMPLATE.md)에 기록.

MCP의 SQL DDL·view DSL은 연결된 Notion 도구 명세와 `notion://docs/view-dsl-spec`에서 확인했다. REST API와 MCP DSL은 서로 다른 입력 형식이다. Python 설치 도구는 `Notion-Version: 2026-03-11`을 명시한다.

컴시간 어댑터는 공개 클라이언트를 확인해 독립적으로 작성했으며 제3자 파서 코드를 복사하지 않았다. 공개 클라이언트 접근과 모의 응답 테스트만으로 실제 학교 조회 성공을 주장하지 않는다. 실제 학교 코드·교사 번호를 제공받지 않았으므로 특정 학교의 주간 시간표 조회 및 실제 Notion 반영 검증은 수행하지 않았다.

NEIS 학사일정은 공식 Open API 응답을 읽고 기존 Notion 일정으로 반영한다. 공개 문서 확인, 모의 응답 테스트, 실제 인증키를 사용한 학교 조회 및 Notion 반영은 서로 다른 검증 단계다. 테스트 결과만으로 실제 학교 전체 일정의 수집·반영이 완료됐다고 보고하지 않는다.
