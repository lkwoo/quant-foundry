# SQLite 갱신 API

## 상태와 범위

새 QuantFoundry DB의 stock, price, price_detail, rs_rating_history 생성·갱신 구현.
price_revisions와 update_runs는 가격 정정과 가격 수집 실행을 기록한다.
기존 QuantTrading DB는 읽기 전용으로 스키마만 확인했으며 수정·이관하지 않았다.
기존 DB를 이 API로 직접 열어 쓰려 하면 거부한다. 새 경로에서 initialize()를 호출한다.

```python
from quantfoundry.stock import (
    Database, PriceBar, update_stock, update_price, update_price_detail,
    update_rs_rating_history, update_market_prices, update_all,
)

db = Database("var/data/quantfoundry.sqlite3")
db.initialize()  # 반복 호출 가능. 기존 비관리 DB는 거부
update_stock(db, "NASDAQ", ["AAPL", "MSFT"])
update_price(db, "NASDAQ", [PriceBar("AAPL", "2024-01-02", 185.64, 82488700)],
             source="example-manual-adjusted-v1")
update_price_detail(db, "NASDAQ")
```

위 가격은 호출 형식 예시다. Yahoo 데이터와 섞지 말고 실사용에는 새 DB에서
아래 provider 경로를 사용한다. source에는 공급자와 조정 정책을 함께 표시한다.

## 네 테이블 함수

| 함수 | 역할 |
|---|---|
| update_stock(db, market, tickers) | 전체 종목 목록 관측 갱신. 사라진 종목은 삭제하지 않고 현재 목록 포함 여부만 변경 |
| update_price(db, market, bars, source=...) | 전달한 가격의 원자적 upsert. 동일 값은 생략, 정정은 이력 기록 |
| update_price_detail(db, market, tickers=None) | SMA/EMA/MACD/Signal/Stage 계산·저장. 변경 없는 종목은 생략 |
| update_rs_rating_history(db, market, sessions, lookback=252) | 마지막 거래일의 동일 시장 RS 갱신. 제외된 짧은 이력 종목 반환 |
| update_market_prices(db, market, sessions, provider=None) | 등록 종목을 Yahoo에서 순차 수집하여 price 갱신. 실패 종목·실행 ID 반환 |
| update_all(db, market, sessions, provider=None, lookback=252) | FDR 목록→가격→지표→RS. 가격 부분 실패 시 파생 갱신 생략 |

## 실 API 호출

```sh
python -m pip install -e ".[market-data]"
quantfoundry init-db --db var/data/quantfoundry.sqlite3
quantfoundry update-stock --db var/data/quantfoundry.sqlite3 --market NASDAQ
quantfoundry update-all --db var/data/quantfoundry.sqlite3 --market NASDAQ --sessions sessions.json
```

sessions.json은 해당 거래소에서 이미 완료된 **실제 거래일**의 오름차순 JSON 배열이다.
예시 형식: ["2024-01-02", "2024-01-03", "2024-01-04"]. RS 252 계산에는
최소 253개 세션이 필요하므로 이 짧은 예시는 실 RS 실행용이 아니다.
휴일을 제외한 거래소 캘린더를 전달해야 한다. 평일 목록을 대신 쓰지 않는다.
자동 캘린더/장 마감 판단은 아직 미구현이며 caller의 책임이다.
가격 수집은 보수적으로 호스트 기준 오늘 이전 날짜만 허용한다.

update-prices, update-details, update-rs도 독립 CLI로 제공한다.
설정 TOML 자동 로딩은 미구현이다. --db/--market/--sessions가 실제 입력이다.

## 신뢰성 정책

- SQLite 표준 라이브러리, FK, UNIQUE, CHECK, BEGIN IMMEDIATE, rollback, 30초 lock timeout.
- 새 DB에 WAL + synchronous=FULL. 연결은 작업 후 닫는다. DB를 로컬 디스크에 둔다.
- 모든 시각은 SQLite CURRENT_TIMESTAMP(UTC), date는 시장 거래일 문자열.
- price에는 adj_close와 공급자 Close를 분리 저장. Close를 무조정 원가격이라고 보장하지 않는다.
- 공급자/조정 정책 혼합은 종목 단위로 거부. Yahoo는 auto_adjust=False를 명시한다.
- 같은 가격 재입력은 행·정정 이력을 늘리지 않는다. 한 배치의 검증·SQL 실패는 전체 롤백.
- 가격 정정·누락 보충 시 해당 종목의 변경일 이후 지표와 시장의 이후 RS를 삭제하여
  오래된 계산이 유효한 것처럼 남지 않게 한다. 지표 재계산 후 필요한 날짜의 RS를 재실행한다.
- update_rs_rating_history는 마지막 입력 거래일 하나만 계산한다. 무효화된 과거 RS를
  자동으로 복원하지 않는다. 과거 universe가 없는 재계산은 당시 순위의 재현이 아니다.
- stock 목록 변경은 과거 RS를 삭제하지 않는다. RS 행에 당시 평가 대상 목록이 저장된다.
- 거래량 0은 허용, 미제공은 NULL. 가격은 양의 유한값, 거래량은 음이 아닌 유한값이어야 한다.
- 정규화 단계에서 가격 보간·결측값 대체를 하지 않는다.

## 계산 정의

SMA 50/120/150/200: N개 관측값 미만이면 NULL.
EMA 5/12/20/26/40: 첫 종가 seed, 이후 alpha=2/(N+1).
MACD: EMA12-26 및 EMA5-20/5-40/20-40. Signal: 각 MACD의 EMA9, 최초 MACD seed.
MACD/Signal의 0을 결측값으로 취급하지 않는다.
Stage: 기존 여섯 순서와 동률 우선순위를 유지한다. 초기 Stage=1이 나올 수 있으므로
전략은 별도 최소 관측 기간/필요한 SMA 존재 여부를 확인해야 한다.
price_detail은 한 종목의 전체 이력을 스트리밍 재계산한다. 메모리는 약 200행이지만
초기 적재와 오래된 가격 정정의 CPU·쓰기 비용은 이력 길이에 비례한다.

RS: 실제 거래일 window 내 모든 가격이 있는 종목만 수익률을 비교한다.
현재일 누락·중간 누락·캘린더 밖 가격은 오류로 처리한다. 저장 이력이 window보다 짧으면
제외 목록으로 반환한다(실제 신규 상장인지 과거 수집 부족인지는 별도 확인 필요).
동률은 낮은 순위 공유, 0~99 percentile, 단일 종목은 0. 기본 252세션이며 lookback을
바꾸면 return_12m 컬럼도 해당 기간 수익률이다. lookback 컬럼을 함께 확인한다.
현재 종목 목록 기반이며 역사적 생존 편향 없는 백테스트를 보장하지 않는다.

## 실행·성능·한계

가격 갱신은 보관 시작일부터 재조회하여 조정가격 변경을 포착한다. 기존 가격 날짜를
공급자가 누락하거나 내부 세션이 빠지면 해당 종목 저장을 거부한다. 신규 종목은 첫
응답 날짜 이전 이력이 없어도 수집하며, 충분한 관측 기간은 지표/RS 단계에서 판단한다.
성공한 종목은 개별 커밋, 실패는 report.failed에 기록한다. 오류를 삼켜 성공 처리하지 않는다.
update_runs의 상태는 가격 수집 단계의 상태다. 지표/RS의 후속 실패는 예외로 전달된다.
전체 시장 작업 하나가 단일 트랜잭션인 것은 아니다. 파생 지표도 종목 단위로 커밋한다.

종목별 순차 수집과 유한 재시도(기본 3회)를 사용한다. full-history 다운로드의
네트워크 비용이 크므로 라즈베리파이 전체 시장 소요시간을 실측한 뒤 정정 탐지 기반
증분 수집으로 확장한다. API 호출 중 DB 트랜잭션을 열어 두지 않는다.
한 DB에는 하나의 갱신 파이프라인만 실행한다. 프로세스 간 전체 작업 잠금과
강제 종료된 RUNNING 실행의 자동 복구는 아직 미구현이다. 재실행은 가격 중복에 안전하다.
실제 공급자 API/ARM 장비 검증과 기존 DB 이관은 이번 변경에 포함되지 않았다.

## 검증

외부 네트워크 없이 임시 SQLite와 공급자 fixture를 사용하여 롤백·멱등성·시장 격리,
정정 이력·지표 재계산·0 처리·SMA 경계·RS 동률/누락·부분 실패/재시도와 네 테이블
연속 갱신을 검증한다. 공급자 adapter는 모의 응답으로 날짜 범위 변환도 확인한다.
