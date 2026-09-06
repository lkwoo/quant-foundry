# QuantFoundry

주식 데이터를 지속적으로 갱신하고, 재현 가능한 조건 평가로 매매 후보를 선정하는 프로젝트.
기존 QuantTrading 코드와 DB는 수정하거나 복사하지 않는다.

## 시작

Python 3.11 이상. 프로젝트 루트에서:

```sh
python -m venv .venv
# 환경 활성화 후
python -m pip install -e .
quantfoundry strategies
python -m unittest discover -s tests -v
```

현재 구현: 패키지 골격, 전략 인터페이스·예제, 네 핵심 SQLite 테이블의 생성·갱신,
가격 정정 이력·지표 무효화/재계산, 거래일 기반 RS, 선택적 FDR/Yahoo adapter,
CLI 및 오프라인 통합 테스트. 예제 전략은 수익성 검증을 거친 투자 전략이 아니다.

DB 사용법과 실행 한계는 [database](docs/database.md)를 참고한다.
아직 미구현: 기존 DB 이관, 후보 결과 저장,
백테스트, 스케줄 실행. config/settings.toml은 update-all이 사용하며 전략 설정 파일은 아직 예시다.

설계는 [architecture](docs/architecture.md), 기존 DB 대응은
[legacy-schema](docs/legacy-schema.md)를 참고한다.

## Windows PowerShell 실행

가상환경을 만드는 것만으로 quantfoundry 명령이 설치되지는 않는다.
프로젝트 루트에서 패키지를 editable 모드로 설치한다.

```powershell
cd "C:\Users\dlrms\OneDrive\바탕 화면\lkw\git\QuantFoundry"
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

시장 하나가 실패해도 다음 시장을 처리하고 결과를 함께 출력한다.
모든 시장 성공이면 종료 코드 0, 실패/부분 실패가 있으면 1이다.
기존 QuantTrading DB는 자동 이관하거나 변경하지 않는다.

## 명시적인 daily 실행

`daily`는 기존 단일 시장 인터페이스를 유지한다.

```powershell
quantfoundry init-db --db var/data/quantfoundry.sqlite3
quantfoundry daily --db var/data/quantfoundry.sqlite3 --market NASDAQ --sessions config/sessions/nasdaq.json
```

이 명령의 sessions 파일은 해당 시장의 완료 거래일 JSON 배열이며 기본 RS에 최소 253개가 필요하다.
간편 실행에는 위의 `update-all`을 권장한다. 두 명령 모두 현재는 데이터 갱신까지 수행하고,
전략 평가와 후보 저장은 미구현이다.

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
