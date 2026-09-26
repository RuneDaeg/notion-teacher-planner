# 근거와 지원 범위

확인일: 2026-09-26. 아래는 구현 시 확인한 1차 자료다. API/제품은 바뀔 수 있으므로 버전을 올릴 때 다시 확인한다.

- [Notion 데이터베이스 생성](https://developers.notion.com/reference/create-a-database): `initial_data_source.properties`로 스키마 생성.
- [Notion 뷰 생성](https://developers.notion.com/reference/create-view): 기존 DB의 뷰 또는 페이지의 linked database view 생성.
- [Notion 뷰 구성](https://developers.notion.com/guides/data-apis/working-with-views): 캘린더 `date_property_id`, `view_range`의 week/month, 그룹 설정, 연결 뷰의 부모 구조.
- [Notion 내부 연결](https://developers.notion.com/guides/get-started/create-a-notion-integration): 토큰과 페이지 접근 설정.
- [컴시간 공식 질의응답: 파일 연동 문의](https://comcigan.co.kr/xe/FAQ/89859): 엑셀 출력에 대한 운영자 답변. 학원시간표 문의이므로 모든 학교 제품의 동일 지원을 보장하는 근거로 쓰지 않음.
- [컴시간 교사 웹 진입점](http://comci.kr/th/), [공개 교사 웹 클라이언트](http://comci.net:4082/th): 공개 클라이언트의 학교·교사 선택, 주별 자료 요청, 날짜·열람 제한·시간표 응답 해석을 확인. 호출 경로와 응답 필드는 변경될 수 있어 동적으로 확인하고, 지원하지 않는 형식에서는 중단한다. 공식 외부 API 계약으로 해석하지 않음. 현재 HTTP로 제공되며 인증 우회는 구현하지 않음.
- 사용자 제공 `PARA of LIGHT` HTML: Inbox/Projects/Areas/Resources/Archives와 Tasks/Notes/Topics 구조를 참고. 원본은 저장소에 배포하지 않음. 교사용 변환 내용은 [PARA.md](PARA.md)에 기록.

MCP의 SQL DDL·view DSL은 연결된 Notion 도구 명세와 `notion://docs/view-dsl-spec`에서 확인했다. REST API와 MCP DSL은 서로 다른 입력 형식이다. Python 설치 도구는 `Notion-Version: 2026-03-11`을 명시한다.

컴시간 어댑터는 공개 클라이언트를 확인해 독립적으로 작성했으며 제3자 파서 코드를 복사하지 않았다. 공개 클라이언트 접근과 모의 응답 테스트만으로 실제 학교 조회 성공을 주장하지 않는다. 실제 학교 코드·교사 번호를 제공받지 않았으므로 특정 학교의 주간 시간표 조회 및 실제 Notion 반영 검증은 수행하지 않았다.
