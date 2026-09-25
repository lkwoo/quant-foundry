# QuantFoundry

주식 데이터를 지속적으로 갱신하고, 재현 가능한 조건 평가로 매매 후보를 선정하는 프로젝트.
기존 QuantTrading 코드와 DB는 수정하거나 복사하지 않는다.

## 현재 구현 상태

| 영역 | 구현 범위 |
| --- | --- |
| 데이터 수집 | KOSPI·KOSDAQ·NASDAQ·NYSE 종목 목록(FDR), 조정종가·종가·거래량(Yahoo) |
| 갱신 작업 | 설정 기반 전체 갱신, 종목 목록의 원자적 교체, 동시 4개 가격 수집, 오류별 재시도·진행 로그 |
| 저장 | SQLite 스키마 v3, 가격 정정 이력, 정정에 따른 지표 무효화·재계산, 한국어 스키마 설명 |
| 지표 | SMA·EMA·MACD·Signal·Stage, 거래일 기반 RS와 명시적인 적격 종목군 계산 |
| 전략 | MA Stage 1·6 Top 10·개별 Stage 조회, HAA-Balanced 목표 비중·판단 근거, 추세 전략 예제 |
| 검증 | 임시 DB와 공급자 대역을 사용하는 오프라인 테스트, 선택적 거래소 캘린더 테스트 |

일일 작업과 전략 평가의 연결, 후보 결과 저장, 기존 DB 이관, 백테스트,
스케줄 실행은 아직 구현하지 않았다. 예제 전략은 수익성 검증을 거친 투자 전략이 아니다.

## 프로젝트 구조

```text
QuantFoundry/
├── src/quantfoundry/         # Python 패키지
│   ├── __main__.py          # python -m quantfoundry 진입점
│   ├── cli.py               # 명령행 인자와 작업 호출
│   ├── settings.py          # TOML 설정 로딩·검증, DB 경로 해석
│   ├── stock.py             # 외부에서 사용하는 DB·갱신 API
│   ├── data/                # 거래일·검증·가격 갱신, downloads.py의 동시 수집·재시도
│   ├── providers/           # FinanceDataReader·Yahoo 공급자 어댑터
│   ├── storage/             # SQLite 스키마·트랜잭션·갱신·스키마 설명
│   ├── indicators/          # 공통 지표 정의와 순수 계산
│   ├── jobs/                # daily·update-all·update-stock, HAA ETF 수집·조회, MA 시장·종목 조회
│   ├── domain/              # Snapshot, RuleResult, Verdict 데이터 모델
│   ├── strategies/          # 전략 규약·추세 전략, HAA 계산, MA Stage·자체 RS 순위
│   └── screening/           # 전략 평가 결과의 후보 여부 판정
├── config/
│   ├── settings.toml        # 갱신 시장·보관 시작일·RS 기간·DB 경로
│   └── strategies/trend.toml # 향후 전략 설정 예시; 현재 로더에서 읽지 않음
├── tests/                   # DB·수집·지표·전략·CLI·캘린더 테스트
├── docs/                    # 설계와 DB 상세 문서
├── pyproject.toml           # 패키지 메타데이터, 선택적 의존성, CLI 등록
├── AGENTS.md                # 저장소 작업·검증·커밋 규칙
└── var/                     # 실행 시 생성되는 로컬 DB 등; Git 제외
```

`stock.py`는 공개 API를 모으는 진입점이며 실제 저장·갱신 구현은
`storage/`와 `data/`에 있다. `domain/`·`indicators/`·`strategies/`는
수집 및 DB 접근과 분리되어 있다.

`update-all`의 실행 흐름:

```text
CLI → 설정 로딩·DB 초기화 → 시장별 완료 거래일 준비
    → 요청 시장의 종목 목록을 모아 한 번에 교체
    → 시장별 가격 병렬 수집 → 완료 순서대로 검증·DB 순차 저장
    → 지표 재계산 → RS 계산 → 결과 출력
```

데이터가 없는 종목과 실제 수집 실패를 구분하며, 유효한 가격이 있는 종목부터 지표를 갱신한다.
RS는 필요한 거래일 가격이 모두 있는 종목만 계산하고 제외 사유·실제 종목군을 기록한다.
종목 목록 교체 후 시장별 갱신 중 발생한 실패는 기록하고 다음 시장을 처리한다.
거래일 준비나 종목 목록 수집·교체 단계의 실패는 전체 작업을 중단한다.

| 문서 | 내용 |
| --- | --- |
| [아키텍처](docs/architecture.md) | 모듈 책임, 전략 확장 원칙, 향후 설계 |
| [DB 사용법](docs/database.md) | Python API·CLI, 데이터 정의, 실행 한계 |
| [스키마 설명](docs/schema-comments.md) | 테이블·컬럼별 한국어 설명 |
| [지표 계산식·검증](docs/price-detail.md) | price_detail의 컬럼별 수식, 초기화·누락 처리, 저장값 검증 결과 |
| [HAA 전략](docs/haa.md) | HAA-Balanced 규칙, 최신일·월말 판단, ETF 수집 및 누락 처리 |
| [이동평균선 전략](docs/ma.md) | 고지로 Stage 조회, Stage 1·6 Top 10 선정 기준, 자체 RS와 기준일 |
| [기존 DB 대응](docs/legacy-schema.md) | 기존 스키마와의 대응 및 이관 시 고려 사항 |

## 시작

Python 3.11 이상. 프로젝트 루트에서:

```sh
python -m venv .venv
# 환경 활성화 후
python -m pip install -e .
quantfoundry strategies
python -m unittest discover -s tests -v
```

기본 설치만으로 SQLite·전략·동시 수집 테스트를 실행할 수 있다. 실제 시세 수집과
캘린더·yfinance 호환성 테스트에는 `python -m pip install -e ".[market-data]"`가 필요하다.
해당 의존성이 없으면 캘린더·yfinance 호환성 테스트는 건너뛴다.
호환성 테스트도 실제 Yahoo 호출 없이 HTTP 모의 응답을 사용한다.

## Windows PowerShell 실행

가상환경을 만드는 것만으로 quantfoundry 명령이 설치되지는 않는다.
프로젝트 루트에서 패키지를 editable 모드로 설치한다.

```powershell
# QuantFoundry 프로젝트 루트에서 실행
# .venv가 없는 경우에만: python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[market-data]"
.\.venv\Scripts\quantfoundry.exe --help
.\.venv\Scripts\quantfoundry.exe daily --help
```

활성화 없이 위처럼 실행 파일 경로를 쓰면 PATH나 PowerShell 스크립트 정책 변경이 필요 없다.
짧은 명령을 사용하려면 현재 PowerShell 세션에서 활성화한다.

```powershell
.\.venv\Scripts\Activate.ps1
quantfoundry --help
```

활성화가 정책으로 차단되면 실행 파일 직접 호출 방식을 사용한다.
모듈 실행도 동일하다: `.\.venv\Scripts\python.exe -m quantfoundry --help`.

## 이동평균선 스테이지 조회

```powershell
quantfoundry -q ma KOSPI    # Stage 1·6 각각 Top 10
quantfoundry -q ma NASDAQ
quantfoundry -q ma 005930   # .KS 생략 가능, KOSDAQ은 .KQ 생략 가능
quantfoundry -q ma AAPL     # 현재 Stage와 EMA 배열·방향
```

KOSPI·KOSDAQ·NASDAQ·NYSE를 지원하며 항상 **판단 기준일**을 표시한다.
고지로의 EMA5·EMA20·EMA40 배열로 Stage를 분류하고, 시장별 Top 10은 같은 날짜의
252거래일 수익률 기반 자체 RS → 수익률 → 종목코드 순으로 정렬한다.
Stage 1은 최신 거래량 양수, 최근 20개 관측일 중 16일 이상 거래 확인, 20일 동일 가격 제외
필터를 추가 적용한다. 단일 종목 조회에서도 필터 제외 사유를 확인할 수 있다.
이 자체 RS는 IBD 공식 RS Rating과 다르며 미너비니의 상대강도 우선 원칙을 참고한 프로젝트 정책이다.
가격·지표는 `update-all`로 갱신하고 조회는 읽기 전용이다. 상세 기준은 [이동평균선 전략](docs/ma.md)을 참고한다.

## HAA 전략 조회

```powershell
quantfoundry update-haa  # 필요한 ETF 10개 가격 수집·갱신
quantfoundry -q haa      # 저장 데이터 최신일 기준 의사 결정과 근거
```

Wouter Keller·Jan Willem Keuning의 HAA-Balanced를 구현한다. 최신 기준일, 목표 비중,
TIP 경보 판단, 자산 순위와 1·3·6·12개월 수익률을 출력한다. 월중에는 잠정 신호와
최근 월말 보유 목표를 구분한다. ETF 가격 누락 시 판단을 보류하고 부족한 종목·날짜를 안내한다.
조회는 읽기 전용이며 실제 주문은 실행하지 않는다. `update-all`과 별도로 ETF를 갱신한다.
상세 규칙과 기준일 정의는 [HAA 전략 문서](docs/haa.md)를 참고한다.

## 전체 갱신: 한 명령

```powershell
quantfoundry update-all
# 활성화하지 않았다면
.\.venv\Scripts\quantfoundry.exe update-all
```

`config/settings.toml`의 시장(KOSPI/KOSDAQ/NASDAQ/NYSE)을 순서대로 처리하며 각 시장의
stock → price → price_detail → rs_rating_history를 갱신한다. 새 DB는 자동 생성한다.
DB 경로는 설정 파일의 상위 프로젝트 폴더 기준이다. 기본 보관 시작일은 2024-01-01이다.
기존 DB에 더 오래된 가격이 있으면 그 날짜까지 재조회한다.
거래일은 exchange_calendars의 한국/미국 거래소 캘린더로 자동 계산하므로 JSON 파일이 필요 없다.
호스트 기준 당일은 제외하며 장 마감 후 2시간이 지난 세션만 대상으로 한다.
캘린더의 최신 임시 휴장 반영 여부와 공급자 데이터 완전성은 별도 점검이 필요하다.

```powershell
# 한 시장만 갱신
quantfoundry update-all --market KOSPI
# DB 경로 변경
quantfoundry update-all --db D:/QuantData/quantfoundry.sqlite3
# 수동 거래일로 실행: 이 경우 단일 시장 필수
quantfoundry update-all --market NASDAQ --sessions config/sessions/nasdaq.json
```

종목 목록 교체를 마친 뒤에는 시장 하나의 갱신이 실패해도 다음 시장을 처리하고 결과를 함께 출력한다.
실제 수집·검증·저장 오류가 없으면 종료 코드 0, 실패/부분 실패가 있으면 1이다.
`NO_DATA`와 누락 거래일 기록만으로 오류 종료하지 않는다. 종료 코드 0이 전체 종목의
데이터 완전성을 뜻하지는 않으므로 결과의 `no_data`, `missing_sessions`, `rs`도 확인한다.
기존 QuantTrading DB는 자동 이관하거나 변경하지 않는다.

## 병렬 수집과 실패 처리

`update-all`은 시장을 순서대로 처리하며, 한 시장 안에서 기본 **4개 종목**을 동시에
다운로드한다. 느린 종목이 있어도 완료된 종목부터 검증·저장하고 빈 작업 자리에 다음
종목을 배정한다. SQLite 저장은 호출 스레드 하나에서 종목별 트랜잭션으로 수행한다.

```powershell
quantfoundry update-all                           # 동시 4개
quantfoundry update-all --workers 2               # 동시 수집 수 축소
quantfoundry update-all --workers 4 --timeout 8 --attempts 2
```

`config/settings.toml`의 `[update]`에서 기본값을 조정한다. CLI 옵션은 설정값을 덮어쓴다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `workers` | `4` | 최대 동시 종목 수. `1`이면 순차 수집 |
| `timeout` | `10` | Yahoo 가격 요청 대기 시간(초) |
| `attempts` | `2` | 재시도 가능한 오류의 총 시도 횟수(최초 1회 포함) |
| `retry_delay` | `2` | 재시도 대기 시작값(초), 추가 재시도마다 2배 |
| `rate_limit_delay` | `30` | 요청 제한 감지 시 새 다운로드를 중단하는 시간(초) |

- Yahoo가 명시적으로 가격 데이터 없음을 반환하거나 정상 구조의 응답에 가격 행이 없는 경우:
  `NO_DATA`로 기록하고 넘어간다. 오류·재시도 대상으로 분류하지 않으며 상장폐지로 확정하거나
  종목·기존 가격을 삭제하지 않는다.
- 일부 기대 거래일의 가격이 없는 경우: 받은 유효 행은 저장하고 `OK_WITH_GAPS`로 표시한다.
  `missing_sessions`에 누락 건수와 날짜 예시(최대 10개)를 기록하며 가격을 채워 넣지 않는다.
- 시간대 조회 실패는 `symbol_lookup`, 비정상 응답은 `provider_response`, 통신 실패는
  `download`, 가격 검증 실패는 `validation`, DB 오류는 `storage`로 구분한다.
  시간대가 없다는 사실만으로 가격 데이터가 없다고 단정하지 않는다.
- 타임아웃·연결 오류·HTTP 5xx: 기본 1회 재시도한다. 대기 중인 재시도는 작업 스레드를 점유하지 않는다.
- 요청 제한: 실행 중인 요청은 마치되 새 요청은 기본 30초 쉬고, 동시 수를 최대 2개로 낮춘다.
  같은 `update-all` 실행의 다음 시장에도 대기 시각과 낮춘 동시 수를 유지한다.
- 캘린더 밖 날짜·중복 날짜·유효하지 않은 가격·기존 저장 날짜의 소실·DB 오류는
  재다운로드하지 않고 실패 처리한다. 누락 날짜만 있는 정상 행과 구별한다.

로그는 stderr에 완료 수·종목·성공/실패·시도 횟수·소요 시간을 출력하며 최종 JSON은 stdout에 출력한다.
`daily`와 `update-prices`도 `--workers`, `--timeout`, `--attempts`를 지원한다.
이 두 명령은 TOML을 읽지 않고 위 기본값과 CLI 옵션을 사용한다.

Yahoo 어댑터는 오류 종류를 보존하기 위해 `Ticker.history()`를 사용하며 yfinance 1.7 계열을 지원한다.
`timeout`은 내부 시간대·인증 조회까지 포함한 종목 전체의 강제 종료 시간이 아니다.
사용자 공급자를 Python API로 주입하면 공급자의 `fetch_prices()`가 동시 호출을 지원해야 하며,
네트워크 타임아웃은 해당 공급자에서 설정한다. 기존 공급자가 순차 호출을 요구하면 `workers=1`을 사용한다.

`prices.succeeded`는 이번에 적재·검증한 종목, `prices.no_data`는 데이터 없는 종목,
`prices.failed`와 `failure_categories`는 실제 오류를 나타낸다. 실행 기록의 JSON에도 함께 저장한다.
실제 가격 수집 실패는 `PARTIAL`/`FAILED`로 남지만 성공 종목의 `price_detail` 계산은 계속한다.

`update-all`·`daily`의 RS는 필요한 기간이 완전한 종목만 계산한다. 이번 실행에서 실패했거나
데이터가 없는 종목은 기존 가격이 남아 있어도 제외한다. 결과에 제외 사유와 실제 종목군을 남기고
계산 버전 `rs-v3-run-eligible-session-window`로 구분한다. 일부 제외는 `PARTIAL_UNIVERSE`,
계산 가능한 종목이 없으면 `INSUFFICIENT_DATA`로 표시하며 순위를 만들지 않는다.
독립 `update-rs` 명령은 기존의 엄격한 기본 검증을 유지한다.

전체 보관 기간 재조회와 가격 정정 검증은 유지한다. 데이터가 없는 날짜의 원인이 휴장·거래정지인지,
공급자 누락인지 자동 확정하지 않는다. 누락을 허용해 적재한 종목의 이동평균은 저장된 관측값 기준이며,
RS는 누락된 거래일을 다른 날로 대체하지 않는다.

## 명시적인 daily 실행

`daily`는 기존 단일 시장 인터페이스를 유지한다.

```powershell
quantfoundry init-db --db var/data/quantfoundry.sqlite3
quantfoundry daily --db var/data/quantfoundry.sqlite3 --market NASDAQ --sessions config/sessions/nasdaq.json
```

이 명령의 sessions 파일은 해당 시장의 완료 거래일 JSON 배열이며 기본 RS에 최소 253개가 필요하다.
간편 실행에는 위의 `update-all`을 권장한다. 두 명령 모두 현재는 데이터 갱신까지 수행하고,
전략 평가를 자동 실행하지 않는다. HAA는 별도 `-q haa` 명령으로 조회하며 후보 저장은 미구현이다.

Raspberry Pi/Linux: `.venv/bin/python -m pip install -e ".[market-data]"` 설치 후
`.venv/bin/quantfoundry update-all`. 실제 ARM 실행은 별도 검증이 필요하다.

## stock만 최신 목록으로 교체

```powershell
quantfoundry update-stock
# 한 시장만 교체: 다른 시장은 유지
quantfoundry update-stock --market KOSPI
```

기본 네 시장의 목록을 모두 조회하고 임시 테이블에 검증·적재한 뒤, 단일 트랜잭션에서
기존 stock을 DELETE 후 새 목록으로 INSERT한다. 조회·적재·반영 중 실패하면 기존
stock의 종목과 update_time은 유지된다. SQLite에는 TRUNCATE가 없어 DELETE를 사용한다.
update-all도 가격 수집 전에 네 시장의 stock을 한 번에 교체한다.

가격 이력은 별도 instruments를 참조한다. 현재 목록에서 빠진 종목의 과거 가격은 보존하며,
가격 적재로 stock에 종목이 다시 추가되지 않는다. 기존 QuantFoundry v1 DB는 initialize 시
트랜잭션으로 v2에 이관한다(stock을 instruments로 rename 후 stock 재생성).
원본 QuantTrading DB의 이관과는 별개이며 그 DB는 수정하지 않는다.

## DB 테이블·컬럼 설명

전체 한국어 설명은 [스키마 설명](docs/schema-comments.md)에 정리했다.
DB에서는 `SELECT * FROM schema_comments`로 조회한다. `init-db` 또는 자동 초기화 시
v1/v2 DB를 v3로 갱신하여 설명을 추가하며 기존 가격·종목 데이터는 변경하지 않는다.
