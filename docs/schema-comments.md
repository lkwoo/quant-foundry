# SQLite 테이블·컬럼 설명

SQLite에는 COMMENT ON이 없다. 아래 설명은 schema_comments 메타데이터에 저장한다.
column_name이 빈 문자열이면 테이블 설명이다. DB 도구의 기본 Comment 칸에 자동 표시되는 기능은 아니다.

```sql
SELECT table_name, column_name, comment FROM schema_comments ORDER BY table_name, column_name;
```

모든 시각은 별도 표시가 없으면 UTC이며 거래일 date는 시장 현지 날짜다. instruments.in_current_listing은 v1 이관 DB에만 존재한다.

## instruments

가격 이력의 참조용 종목 마스터. 현재 목록에서 제외돼도 유지하여 과거 price의 외래키를 보존한다.

| 컬럼 | 설명 |
|---|---|
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `update_time` | UTC 기록 시각. 신규 키를 마스터에 처음 등록할 때 저장한다. v1 이관 행은 당시 stock 갱신 시각이며 현재 목록의 최신 조회 시각을 뜻하지 않는다. |
| `in_current_listing` | v1 DB에서 stock을 rename한 경우에만 남는 호환 컬럼. 이관 당시 목록 포함 여부로, 이후 최신화하지 않으므로 현재 목록 판단에는 사용하지 않는다. 신규 DB에는 없다. |

## stock

최근 성공적으로 조회한 시장별 종목 목록 스냅샷. 임시 테이블 검증 후 대상 시장의 전체 목록을 원자적으로 교체한다.

| 컬럼 | 설명 |
|---|---|
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `update_time` | 현재 종목 목록을 성공적으로 반영한 UTC 시각. YYYY-MM-DD HH:MM:SS. 원자적 교체가 실패하면 이전 값이 유지된다. |
| `in_current_listing` | 목록 포함 여부를 나타내는 호환 플래그(0/1). 전체 교체에 성공한 stock의 행은 모두 1이며 제외된 종목은 stock에서 삭제된다. |

## price

종목별 일일 가격 원천 데이터. (ticker, market, date)가 유일하며 정정 시 값을 갱신하고 파생 지표를 무효화한다.

| 컬럼 | 설명 |
|---|---|
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `date` | 시장 현지 기준 거래일. YYYY-MM-DD 문자열이며 UTC 저장 시각과 구분한다. |
| `adj_close` | 공급자가 제공한 조정 종가. 양의 유한값이며 통화는 해당 종목 기준이다. 조정 정책은 price.source를 참조한다. |
| `volume` | 해당 거래일의 공급자 거래량. 0은 유효하며 미제공은 NULL이다. SQLite REAL로 저장하고 종목/공급자의 거래 단위를 따른다. |
| `close` | 공급자의 Close 값. 현재 Yahoo 연결은 auto_adjust=False로 요청하여 Adj Close와 분리한다. 공급자 자체 조정이 없는 원시 체결가격임을 보장하지 않는다. 미제공은 NULL이다. |
| `source` | 가격 공급자와 조정 정책의 식별 문자열. 예: yahoo-adj-close-auto_adjust_false-v1. 같은 종목에서 다른 정책의 가격을 무검증 혼합하지 않는다. |
| `insert_time` | 가격 행을 최초 저장하거나 값이 달라져 갱신한 UTC 시각. 동일 값 재입력에서는 바뀌지 않으며 거래일을 뜻하지 않는다. |

## price_detail

price.adj_close 기반의 일별 기술적 지표. 가격 정정 이후 무효화되며 종목별 전체 이력을 재계산해 저장한다. 초기 계산값의 존재가 전략의 준비 기간 충족을 뜻하지 않는다.

| 컬럼 | 설명 |
|---|---|
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `date` | 시장 현지 기준 거래일. YYYY-MM-DD 문자열이며 UTC 저장 시각과 구분한다. |
| `adj_close` | 지표 계산에 사용한 해당 거래일의 조정 종가. price.adj_close를 복사한 값이다. |
| `volume` | 지표 재계산 시 복사한 해당 거래일의 price.volume. 미제공은 NULL이다. |
| `stage` | EMA5/20/40 정렬 단계. 1:5≥20≥40, 2:20≥5≥40, 3:20≥40≥5, 4:40≥20≥5, 5:40≥5≥20, 6:5≥40≥20. 동률은 먼저 만족하는 단계 우선. 첫날도 1이 될 수 있다. 저장 타입은 REAL이다. |
| `sma_50` | 최근 50개 가격 관측값의 adj_close 단순평균. 관측값이 50개 미만이면 NULL. 캘린더상 경과 일수가 아니라 저장된 관측값 개수 기준이다. |
| `sma_120` | 최근 120개 가격 관측값의 adj_close 단순평균. 관측값이 120개 미만이면 NULL. 캘린더상 경과 일수가 아니라 저장된 관측값 개수 기준이다. |
| `sma_150` | 최근 150개 가격 관측값의 adj_close 단순평균. 관측값이 150개 미만이면 NULL. 캘린더상 경과 일수가 아니라 저장된 관측값 개수 기준이다. |
| `sma_200` | 최근 200개 가격 관측값의 adj_close 단순평균. 관측값이 200개 미만이면 NULL. 캘린더상 경과 일수가 아니라 저장된 관측값 개수 기준이다. |
| `ema_5` | adj_close의 5기간 지수이동평균. 첫 종가로 초기화하며 EMA_t = EMA_prev + 2/(5+1) × (price_t - EMA_prev). 준비 기간은 별도 전략 조건이다. |
| `ema_12` | adj_close의 12기간 지수이동평균. 첫 종가로 초기화하며 EMA_t = EMA_prev + 2/(12+1) × (price_t - EMA_prev). 준비 기간은 별도 전략 조건이다. |
| `ema_20` | adj_close의 20기간 지수이동평균. 첫 종가로 초기화하며 EMA_t = EMA_prev + 2/(20+1) × (price_t - EMA_prev). 준비 기간은 별도 전략 조건이다. |
| `ema_26` | adj_close의 26기간 지수이동평균. 첫 종가로 초기화하며 EMA_t = EMA_prev + 2/(26+1) × (price_t - EMA_prev). 준비 기간은 별도 전략 조건이다. |
| `ema_40` | adj_close의 40기간 지수이동평균. 첫 종가로 초기화하며 EMA_t = EMA_prev + 2/(40+1) × (price_t - EMA_prev). 준비 기간은 별도 전략 조건이다. |
| `macd` | EMA12 - EMA26. 표준 MACD 선이며 0과 음수도 정상값이다. |
| `macd_5_20` | EMA5 - EMA20. 단기·중기 추세 차이이며 0과 음수도 정상값이다. |
| `macd_5_40` | EMA5 - EMA40. 단기·장기 추세 차이이며 0과 음수도 정상값이다. |
| `macd_20_40` | EMA20 - EMA40. 중기·장기 추세 차이이며 0과 음수도 정상값이다. |
| `signal` | macd의 9기간 EMA. 최초 MACD 값으로 초기화하고 alpha=2/(9+1)을 적용한다. 0을 결측값으로 취급하지 않는다. |
| `signal_5_20` | macd_5_20의 9기간 EMA. 최초 MACD 값으로 초기화하고 alpha=2/(9+1)을 적용한다. 0을 결측값으로 취급하지 않는다. |
| `signal_5_40` | macd_5_40의 9기간 EMA. 최초 MACD 값으로 초기화하고 alpha=2/(9+1)을 적용한다. 0을 결측값으로 취급하지 않는다. |
| `signal_20_40` | macd_20_40의 9기간 EMA. 최초 MACD 값으로 초기화하고 alpha=2/(9+1)을 적용한다. 0을 결측값으로 취급하지 않는다. |
| `calculation_version` | 지표 계산 규칙의 버전. 현재 daily-v1-first-close-seed. EMA 초기값·기간·동률 정책 등이 달라지면 버전을 변경하여 재계산한다. |
| `insert_time` | 해당 지표 행을 재계산하여 저장한 UTC 시각. price의 조회 시각이나 전략 신호 발생 시각이 아니다. |

## rs_rating_history

같은 시장·기준일의 유효 종목 집합 내 수익률 상대 순위. 현재 종목 목록으로 계산하므로 과거 시점의 전체 상장 종목을 재현하는 백테스트 자료는 아니다.

| 컬럼 | 설명 |
|---|---|
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `date` | RS 평가 기준 거래일(전달된 완료 거래일 목록의 마지막 날짜). YYYY-MM-DD. |
| `rs_percentile` | 0~99 정수 상대 순위. 수익률 오름차순의 0기반 순위 r로 min(99, floor(100*r/(N-1))) 계산. 동률은 가장 낮은 순위 공유, 단일 종목은 0이다. |
| `return_12m` | 기준일 adj_close / lookback 거래세션 전 adj_close - 1. 0.1은 10% 수익률이다. 기본 lookback=252이나 다른 값을 지정하면 이름과 달리 해당 기간 수익률을 저장한다. |
| `lookback` | 수익률 계산에 사용한 거래세션 간격. 기본 252이며 시작일·종료일 포함 lookback+1개 가격 관측값이 필요하다. |
| `universe_size` | 해당 시장·기준일 순위 산정에 실제 참여한 종목 수. 이력이 부족해 제외된 종목은 포함하지 않는다. |
| `universe_json` | 해당 순위 계산에 참여한 티커의 정렬된 JSON 배열. 비교 집합을 기록하며 거래소 전체 상장 종목 목록과 다를 수 있다. |
| `calculation_version` | RS 계산 정의의 버전. 현재 rs-v1-session-window. 거래일 완전성·동률·백분위 규칙의 추적에 사용한다. |
| `insert_time` | RS 결과를 저장한 UTC 시각. 같은 기준일을 다시 계산하면 새 저장 시각으로 교체된다. |

## price_revisions

기존 가격 행의 값이 변경될 때 남기는 정정 이력. 최초 삽입·동일 값 재입력은 기록하지 않으며 가격 변경과 같은 트랜잭션으로 저장한다.

| 컬럼 | 설명 |
|---|---|
| `id` | 가격 정정 이력의 INTEGER PRIMARY KEY. SQLite가 생성하는 행 식별자이며 빈 번호 없이 증가함을 보장하지 않는다. |
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `ticker` | 공급자 조회에 사용하는 종목 티커. 국내는 .KS/.KQ 접미사를 사용한다. 시장이 다르면 같은 문자열이 별도 종목일 수 있다. |
| `date` | 정정 대상 가격의 거래일. YYYY-MM-DD이며 정정 처리 시각은 changed_at에 저장한다. |
| `old_json` | 정정 전 adj_close, volume, close, source를 담은 JSON 객체. 결측값은 JSON null이다. |
| `new_json` | 정정 후 adj_close, volume, close, source를 담은 JSON 객체. old_json과 비교하여 변경 내용을 확인한다. |
| `changed_at` | 가격 정정을 저장한 UTC 시각. 과거 가격이 공급자에서 실제 변경된 시각을 뜻하지 않는다. |

## update_runs

시장별 가격 수집 작업 실행 기록. 종목 목록·지표·RS를 포함한 전체 작업의 성공 기록은 아니다. 강제 종료된 RUNNING은 자동 복구되지 않는다.

| 컬럼 | 설명 |
|---|---|
| `id` | 가격 수집 실행의 UUID4 hex 식별자(32자리 문자열). 로그와 실행 결과를 연결하는 키다. |
| `market` | 주식 상장 시장. KOSPI, KOSDAQ, NASDAQ, NYSE 중 하나이며 ticker와 함께 종목을 식별한다. |
| `start_date` | 이번 가격 조회 요청의 시작 거래일. YYYY-MM-DD, 포함 범위이며 공급자별 상장 이력은 더 짧을 수 있다. |
| `as_of` | 이번 가격 조회 요청의 마지막 완료 거래일. YYYY-MM-DD, 포함 범위. 요청 목표일이며 모든 종목의 저장 성공을 보장하지 않는다. |
| `status` | 가격 수집 단계 상태: RUNNING 진행 중, SUCCESS 모든 대상 성공, PARTIAL 성공·실패 혼재, FAILED 전체 실패 또는 중단. 지표/RS 단계 상태와는 별개다. |
| `result_json` | 실행 결과 JSON: run_id, status, inserted, revised, unchanged, succeeded(티커 배열), failed(티커별 오류 객체). 시작 직후나 강제 종료 시 NULL일 수 있다. |
| `started_at` | 가격 수집 실행 기록을 생성한 UTC 시각. |
| `finished_at` | 가격 수집 실행 결과를 기록한 UTC 시각. 실행 중 또는 결과 기록 전 강제 종료되면 NULL이다. |

## schema_comments

SQLite의 COMMENT ON 대신 사용하는 한국어 스키마 설명 메타데이터. table_name과 column_name의 조합이 유일하며 column_name이 빈 문자열이면 테이블 설명이다.

| 컬럼 | 설명 |
|---|---|
| `table_name` | 설명 대상의 실제 SQLite 테이블 이름. |
| `column_name` | 설명 대상 컬럼 이름. 빈 문자열('')은 테이블 자체의 설명을 의미한다. NULL은 사용하지 않는다. |
| `comment` | 테이블 또는 컬럼의 한국어 설명. 의미·단위·NULL·계산 정의·운영 한계를 포함한다. |
| `updated_at` | 설명이 최초 등록되거나 내용이 변경된 UTC 시각. 같은 설명을 재적용하면 변경하지 않는다. |
