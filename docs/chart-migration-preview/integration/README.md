# 실제 페이지 차트 적용

2026-09-11. 캔들·자산곡선을 Lightweight Charts **5.2.1**로 이식했다. `indicators.js`의 전략 계산과 시세 API는 그대로 사용한다.

## 바로 확인

- [직접 만들기 · PC, RSI](page-builder-rsi-1440.png)
- [직접 만들기 · 모바일](page-builder-390.png)
- [내 에이전트 · PC](page-agents-1440.png)
- [내 에이전트 · 모바일](page-agents-390.png)
- [백테스트 한도 초과 중에도 표시되는 차트](studio-backtest-budget-desktop.png)
- [모바일 자산곡선 · 1,000개 데이터](equity-mobile-1000-points.png)

## 보존한 내용

전략 A–K의 가격선·범례·설명·음영·매수/매도 삼각형, RSI 기준값·행동 문구, 이동평균, 실행 세션의 내 평단을 유지한다. 전체 300봉으로 지표를 계산하며, 확대해도 계산 전 구간을 버리지 않는다. OHLC·봉별 등락률·시각·표시 구간·LIVE·현재 가격선과 봉 간격 선택, 확대·이동·키보드 조회도 유지한다.

과거 구간은 봉의 시각으로 고정한다. 실시간 데이터로 300봉 버퍼가 교체되어도 보고 있던 기간을 유지하며, 시세 조회가 일시 실패해도 정상적으로 받은 차트는 남는다. RSI 계산 전 값은 빈 구간으로 처리한다.

자산곡선은 시작/최종 날짜·금액, 초기자본선, 마지막 점, 면 색상, 날짜별 금액 조회를 보존한다. 반복 날짜와 모바일의 긴 데이터도 누락하지 않는다.

## 백테스트 한도와 차트

20,000봉 제한은 차트 라이브러리 도입 전부터 있던 동기 백테스트의 제한이다(`3a3af87`). 1년·1분봉은 525,600개다. 실시간 차트는 별도의 `/api/candles`로 최근 300봉을 조회한다.

`/api/backtest/limits`가 실제 서버 한도·봉 간격·기간 일수를 제공한다. 화면에서는 백테스트 실행 전에 개수를 표시하고, 초과하는 백테스트만 막는다. 기간 유지/봉 간격 유지 버튼은 사용자가 선택해야 적용된다. 자동 백테스트가 켜져 있거나 백테스트 서버가 실패해도 선택한 봉 간격의 차트와 전략선은 계속 표시한다.

## 검증 결과

| 검증 | 결과 | 기록 |
| --- | --- | --- |
| 전략·포지션·평단·표시 모드, PC/모바일 | 154개 조합 통과 | [report.json](report.json) |
| 확대·이동·갱신·키보드·오류 복구·터치 스크롤 | 18개 그룹 통과 | [report.json](report.json) |
| 실제 `/builder`, `/agents` 화면 | 6개 통과 | [pages-report.json](pages-report.json) |
| 자산곡선 | 7개 그룹 통과 | [equity-report.json](equity-report.json) |
| 백테스트 한도와 차트 독립 표시 | 6개 그룹 통과 | [studio-backtest-budget-report.json](studio-backtest-budget-report.json) |
| 데이터 변환·전략 primitive·한도 계산 | JS 테스트 20개 통과 | `frontend/tests/*ChartData.test.js`, `chartStrategyPrimitive.test.js`, `backtestBudget.test.js` |
| 서버 메타데이터·기간·기존 시세 처리 | Python 테스트 20개 통과 | `backend/tests/test_backtest_limits.py`, `test_market.py` |
| 프런트엔드 프로덕션 빌드 | 통과 | Vite |

브라우저 검증은 실제 컴포넌트를 사용하고 API 응답을 테스트 데이터로 대체했다. 실제 거래나 운영 DB에 쓰는 검증은 포함하지 않는다. [보존 기준과 표시 방식 변경](checklist.md)도 함께 기록했다.

로컬 Vite 실행 후 검증 스크립트의 `CHART_TEST_BASE_URL`, `EQUITY_CHART_URL`을 실행 주소에 맞춰 사용한다. 결과 이미지와 보고서는 이 폴더에 저장한다.

## 라이선스

차트의 TradingView 링크를 유지하며, 배포되는 `public/licenses/lightweight-charts-NOTICE.txt`와 `lightweight-charts-LICENSE.txt`에 원 저작권 고지와 Apache 2.0 라이선스를 포함했다. [공식 저장소](https://github.com/tradingview/lightweight-charts).
