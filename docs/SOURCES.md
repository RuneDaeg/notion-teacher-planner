# 근거와 지원 범위

확인일: 2026-09-26. 아래는 구현 시 확인한 1차 자료다. API/제품은 바뀔 수 있으므로 버전을 올릴 때 다시 확인한다.

- [Notion 데이터베이스 생성](https://developers.notion.com/reference/create-a-database): `initial_data_source.properties`로 스키마 생성.
- [Notion 뷰 생성](https://developers.notion.com/reference/create-view): 기존 DB의 뷰 또는 페이지의 linked database view 생성.
- [Notion 뷰 구성](https://developers.notion.com/guides/data-apis/working-with-views): 캘린더 `date_property_id`, `view_range`의 week/month, 그룹 설정, 연결 뷰의 부모 구조.
- [Notion 내부 연결](https://developers.notion.com/guides/get-started/create-a-notion-integration): 토큰과 페이지 접근 설정.
- [컴시간 공식 질의응답: 파일 연동 문의](https://comcigan.co.kr/xe/FAQ/89859): 엑셀 출력에 대한 운영자 답변. 학원시간표 문의이므로 모든 학교 제품의 동일 지원을 보장하는 근거로 쓰지 않음.
- 사용자 제공 `PARA of LIGHT` HTML: Inbox/Projects/Areas/Resources/Archives와 Tasks/Notes/Topics 구조를 참고. 원본은 저장소에 배포하지 않음. 교사용 변환 내용은 [PARA.md](PARA.md)에 기록.

MCP의 SQL DDL·view DSL은 연결된 Notion 도구 명세와 `notion://docs/view-dsl-spec`에서 확인했다. REST API와 MCP DSL은 서로 다른 입력 형식이다. Python 설치 도구는 `Notion-Version: 2026-03-11`을 명시한다.
