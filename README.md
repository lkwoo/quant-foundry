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
아직 미구현: 기존 DB 이관, 거래소 캘린더 자동 조회, 설정 자동 로딩, 후보 결과 저장,
백테스트, 스케줄 실행. config 파일은 설계용 예시다.

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

## daily 실행

```powershell
.\.venv\Scripts\quantfoundry.exe init-db --db var/data/quantfoundry.sqlite3
.\.venv\Scripts\quantfoundry.exe daily --db var/data/quantfoundry.sqlite3 --market NASDAQ --sessions config/sessions/nasdaq.json
```

`config/sessions/nasdaq.json`은 사용자가 준비할 해당 거래소의 완료 거래일 JSON 배열이다.
파일을 자동 생성하거나 평일을 거래일로 추정하지 않는다. 최소 253개 거래일이 필요하며,
이미 저장한 가장 오래된 가격부터 최종 갱신일까지 포함한다. 한국 시장에는 한국 거래일
파일을 별도로 사용한다. 거래일 형식과 누락 정책은 [DB 사용법](docs/database.md)을 참고한다.

호출 경로: `quantfoundry daily` → `cli.main()` → `jobs.daily.run_daily()` → `update_all()`.
현재 daily는 종목 목록·가격·지표·RS 갱신까지 수행한다. 전략 평가와 후보 저장은 미구현이다.
정상 종료 코드는 0, 부분 실패·오류는 1이다. 인자 오류는 argparse의 2를 사용한다.

Raspberry Pi/Linux에서는 `python3 -m venv .venv`, `.venv/bin/python -m pip install -e ".[market-data]"`
후 `.venv/bin/quantfoundry daily ...`로 실행한다. 실제 ARM 실행은 별도 검증이 필요하다.
