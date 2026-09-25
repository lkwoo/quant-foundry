# price_detail 계산식과 검증

모든 가격 지표의 입력은 같은 `(market, ticker)`의 `price.adj_close`를 날짜 오름차순으로
정렬한 값이다. 아래에서 `P_t`는 t번째 저장 관측값이며, 휴장이나 누락 날짜에 값을 보간하지 않는다.
`close` 컬럼은 계산에 사용하지 않는다. 현재 계산 버전은 `daily-v1-first-close-seed`다.

## 컬럼별 정의

| 컬럼 | 계산 또는 저장 규칙 |
| --- | --- |
| `ticker`, `market`, `date` | 해당 원천 가격 행의 식별자·거래일 복사 |
| `adj_close` | `P_t = price.adj_close` 복사 |
| `volume` | `price.volume` 복사. 0은 0, 미제공은 NULL 유지 |
| `sma_50` | 최근 50개 `P`의 합 / 50 |
| `sma_120` | 최근 120개 `P`의 합 / 120 |
| `sma_150` | 최근 150개 `P`의 합 / 150 |
| `sma_200` | 최근 200개 `P`의 합 / 200 |
| `ema_5` | `E_t = E_(t-1) + (2/6) × (P_t - E_(t-1))` |
| `ema_12` | `E_t = E_(t-1) + (2/13) × (P_t - E_(t-1))` |
| `ema_20` | `E_t = E_(t-1) + (2/21) × (P_t - E_(t-1))` |
| `ema_26` | `E_t = E_(t-1) + (2/27) × (P_t - E_(t-1))` |
| `ema_40` | `E_t = E_(t-1) + (2/41) × (P_t - E_(t-1))` |
| `macd` | `ema_12 - ema_26` |
| `macd_5_20` | `ema_5 - ema_20` |
| `macd_5_40` | `ema_5 - ema_40` |
| `macd_20_40` | `ema_20 - ema_40` |
| `signal` | `macd`의 9기간 EMA |
| `signal_5_20` | `macd_5_20`의 9기간 EMA |
| `signal_5_40` | `macd_5_40`의 9기간 EMA |
| `signal_20_40` | `macd_20_40`의 9기간 EMA |
| `stage` | EMA5·EMA20·EMA40의 순서에 따른 아래 1~6 단계 |
| `calculation_version` | 계산 규칙 버전 문자열 |
| `insert_time` | 해당 지표 행을 저장한 UTC 시각. 원천 가격 날짜와 구분 |

SMA는 관측값이 N개 미만이면 NULL이고 N번째부터 계산한다. EMA는 모든 기간에서
첫 관측값 `E_0 = P_0`로 초기화한다. Signal은 첫 MACD 값으로 시작하며 이후
`S_t = S_(t-1) + 0.2 × (MACD_t - S_(t-1))`를 적용한다.
현재 초기화 규칙상 첫날의 모든 MACD·Signal은 0이다. 0과 음수는 유효한 계산값이다.

| Stage | 조건 |
| --- | --- |
| 1 | EMA5 ≥ EMA20 ≥ EMA40 |
| 2 | EMA20 ≥ EMA5 ≥ EMA40 |
| 3 | EMA20 ≥ EMA40 ≥ EMA5 |
| 4 | EMA40 ≥ EMA20 ≥ EMA5 |
| 5 | EMA40 ≥ EMA5 ≥ EMA20 |
| 6 | EMA5 ≥ EMA40 ≥ EMA20 |

동률로 여러 조건을 만족하면 표의 앞선 단계를 선택한다. 첫날은 세 EMA가 같아 Stage 1이다.
이는 충분한 이력을 갖춘 상승 추세라는 뜻이 아니므로 전략의 준비 기간은 따로 판단해야 한다.

## 비교할 때 주의할 기준

- EMA는 첫 N개 값의 SMA로 초기화하는 방식과 다르다. pandas로 비교하려면 `ewm(span=N, adjust=False)`를 사용한다.
- SMA·EMA의 기간은 저장 관측값 개수다. 누락일이 있으면 최근 200개 가격이 200개 거래세션보다 긴 기간에 걸칠 수 있다.
- EMA 시작점은 DB에 보관된 최초 가격이다. 더 긴 과거 이력을 사용하는 차트 서비스와 초기 구간 값이 다를 수 있다.
- 조정종가를 사용하므로 공급자 Close 기준 지표와 다를 수 있다.
- 가격 정정·누락 보충 시 해당 종목의 전체 이력을 다시 계산해 EMA·Signal의 연속성을 유지한다.

## 검증 기록 — 2026-09-25

컬럼 정의, 계산 결과의 순서, SQL INSERT 컬럼의 대응을 검토했으며 계산식 변경이 필요한 오류는 발견하지 못했다.

운영 DB를 읽기 전용의 동일 스냅샷으로 조회해 KOSPI·KOSDAQ·NASDAQ·NYSE에서 각 3개,
총 12개 종목의 8,091행을 독립 pandas rolling/ewm 계산과 비교했다. 비교 대상에는
SMA 4개·EMA 5개·MACD 4개·Signal 4개, Stage, 복사 컬럼과 버전이 포함됐다.
표본 내 누락 지표 행이나 허용 오차 초과 불일치는 없었다. 수치 최대 절대 차이는 약
`2.57e-9`로 부동소수점 연산 순서 차이 범위였다(`atol=1e-8`, `rtol=1e-12`).
이는 전 종목·전 행에 대한 전수 검증은 아니다.

`tests/test_indicator_formulas.py`는 표준 라이브러리 Decimal의 50자리 정밀도와
EMA의 전개된 가중합을 사용해 실제 저장 컬럼을 독립적으로 검증한다. 260개 관측값의
모든 SMA 경계, EMA 초기화, 네 MACD·Signal 쌍, Stage 6가지 순서와 동률,
0·음수·NULL, 종목·시장 간 초기 상태 분리를 확인한다.

```sh
python -m unittest discover -s tests -p test_indicator_formulas.py -v
```
