# GLOEUM v2.30.3 Apify 검색 시간초과 복구 가이드

## 확인된 원인

연결 테스트는 약 16초에 정상 완료됐으므로 API 토큰·권한·크레딧 문제가 아닙니다. 실제 후보 검색은 여러 검색어에 각각 `searchLimit`을 적용해 처리량이 커졌고, Search Actor가 120초를 넘기면서 `HTTP 400 / status: TIMED-OUT`으로 종료됐습니다. v2.30.2는 이 응답을 재시도 불가 요청 오류로 분류했습니다.

## 수정 내용

1. `HTTP 400` 안의 `TIMED-OUT`을 일시 오류로 판별
2. 목표 20개 이하: 핵심 검색어 최대 3개·검색어당 최대 8건
3. 시간초과 시 핵심 검색어 1개·최대 6건으로 1회 자동 재시도
4. 오류 상태창을 자동으로 펼쳐 원인 표시
5. 기존 프로필 검증·국가/업종 판정·Excel 출력 유지

## GitHub와 Render

1. `GLOEUM_v2.30.3_update_only.zip`을 풉니다.
2. GitHub `main`에서 **Add file → Upload files**로 압축을 푼 파일을 덮어씁니다.
3. 커밋 메시지: `v2.30.3 apify search timeout recovery`
4. Render 자동배포 완료 후 화면 버전 `v2.30.3`과 `Live`를 확인합니다.

`APIFY_TOKEN`, 기존 Render 서비스, 기존 운영 URL은 변경하지 않습니다.

## 첫 검수 조건

- 국가: Vietnam
- 분야: 뷰티·화장품
- 후보 수: 10개
- Apify 원본 프로필 자동검증: 켬
- 웹 검색 보강: 끔
- 외부 공개출처 교차검증: 끔

정상 기준은 Apify Runs에 Search Actor 실행이 새로 생기고, 앱이 오류 대신 결과 또는 검토대기 후보를 표시하는 것입니다. 최초 시험에서는 같은 버튼을 연속 클릭하지 마십시오.

## 자동검사 결과

- 핵심/회귀검사: 50개 통과
- 전체 오프라인 검사: 71개 통과

```bash
python -m unittest -q test_v2241.py test_v2270.py test_v2300.py test_v2301.py test_v2302.py test_v2303.py
python run_offline_tests.py
```
