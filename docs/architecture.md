# 구조와 확장 원칙

## 목표

수집 결과와 데이터 품질, 지표 정의, 전략 파라미터, 후보 선정 근거를 추적한다.
단일 Python 패키지와 SQLite를 사용하며, 운영과 연구가 동일한 계산·전략 코드를 공유한다.

## 계층

- domain/: 시장·종목·기준일·데이터 버전 및 결과 모델. 외부 API와 SQL 의존 금지.
- providers/: FDR 종목 목록, Yahoo 가격, 공급자별 심볼 변환. 재시도와 응답 정규화.
- data/: 종목 목록 이력, 거래일, 품질 검사, 누락 복구, 가격 갱신 계획.
- storage/: SQLite 연결과 트랜잭션, 목적별 repository, 명시적 migrations.
- indicators/: 순수 SMA/EMA/MACD/Signal/Stage/RS 계산. DB와 현재 시간 참조 금지.
- strategies/: 전략별 입력 요구사항·버전·조건 평가. API 조회와 SQL 금지.
- screening/: 필요한 공통 지표를 한 번 계산하고 각 전략에 동일한 snapshot 제공.
- backtest/: 과거 시점 snapshot, 체결·비용·포트폴리오 상태. 전략 평가 로직 재사용.
- jobs/: 수집→검증→저장→지표→전략→결과 공개 순서와 실행 상태 관리.
- cli.py: 작업 호출 입구. 계산 구현을 넣지 않는다.

일일 흐름: 종목 갱신 → 완료 거래일 결정 → 종목별 수집 계획 → 가격 검증·저장
→ 변경 구간 지표 재계산 → 시장 완전성 확인 → 전략 평가 → 후보 및 근거 저장.
수집 실패 종목을 조용히 제외하여 RS 모집단을 바꾸지 않는다.

## 전략 추가

1. strategies/에 Strategy 규약을 구현한다.
2. id, version, required_features를 선언한다.
3. evaluate(snapshot)는 조건별 PASS/FAIL/UNKNOWN과 근거를 반환한다.
4. registry.py에 명시적으로 등록하고 경계 조건 테스트를 추가한다.
5. 향후 파라미터 설정을 검증한 뒤 평가기에 주입하고 설정 해시를 실행 결과에 저장한다.

서로 다른 이동평균 기간은 feature 명세(이름·파라미터·버전·가격 기준)로 구분한다.
전략별로 price_detail 컬럼을 계속 추가하지 않는다. 공통 지표는 재사용하고,
임의 기간 지표는 별도 feature_values 저장소 또는 계산 캐시로 확장한다.
단일 종목 전략은 Snapshot, 시장 순위/자산배분 전략은 동일 기준일의
UniverseSnapshot과 포트폴리오 상태를 받는 별도 규약을 추가한다.
기존 단일 종목 규약에 모든 전략을 억지로 맞추지 않는다.

## 신뢰성 계약

종목은 내부 ID로 식별하고 공급자 심볼·시장 변경 이력을 별도 관리한다.
거래일은 시장 현지 날짜, 관측·저장 시각은 UTC로 구분한다.
원종가·조정종가·기업행사를 구분하고 공급자의 조정 의미를 문서화한다.
최근 구간 재조회와 과거 정정 점검을 수행한다. 수정 시점부터 지표를 무효화하고
SMA에는 직전 window, EMA에는 수정 이전의 유효 상태를 사용해 재계산한다.
0은 결측값이 아니다. 휴장·정지·누락을 구별하고 가격을 무조건 보간하지 않는다.
EMA seed, 준비 기간, Stage 동률, RS 모집단과 순위 동률 정책을 버전으로 고정한다.

실행 상태는 SUCCESS/PARTIAL/FAILED. 품질 기준 미달 실행은 후보 공개를 보류한다.
결과에는 as_of, strategy/version, parameters, data_version, indicator_version,
조건별 값·판정·이유, 코드 버전을 기록한다. 가격 revision이나 고정 snapshot으로
기존 결과에 사용된 입력을 복원할 수 있어야 한다.

## 저장 확장 계획

신규 DB에 schema_migrations, instruments, listing_observations, daily_prices,
price_revisions, indicator_values/feature_values, update_runs/update_items,
quality_issues, screening_runs/screening_results를 단계적으로 만든다.
종목+거래일 uniqueness 및 조회 인덱스를 명시하고 SQL은 바인딩 파라미터를 사용한다.
기존 DB는 읽기 전용 이관 입력이다. 자동으로 운영 DB로 열거나 변경하지 않는다.

## Raspberry Pi

Python 3.11+ 지원 64-bit OS/장비를 우선 대상으로 삼되 실제 ARM 의존성 설치를 검증한다.
로컬 SSD, 낮은 수집 동시성, 단일 DB writer, 짧은 트랜잭션을 기본으로 한다.
WAL/busy_timeout/foreign_keys를 연결 정책으로 검토하고 장애 내구성 설정을 명시한다.
DB는 동기화 폴더 밖에 두고 일관된 SQLite backup API 결과만 외부로 복사한다.
systemd timer, 중복 실행 잠금, 실행 체크포인트, 백업 복원 검증을 운영 단계에 추가한다.
신규 종목 과거 보충과 일일 갱신을 분리한다. 대규모 백테스트는 PC에서 실행한다.

## 구현 순서와 검증

1. 실제 SQLite PRAGMA로 스키마 읽기 전용 확인, 지표·가격 정의 확정.
2. 새 DB migrations/repository + rollback/중복 실행/가격 정정 통합 테스트.
3. 공급자 adapter + fixture 테스트 + 소규모 실 API 확인 + 품질 검사.
4. 순수 지표 + 손계산 fixture + 전체/증분/정정 후 재계산 일치 검증.
5. 공통 feature 계산과 복수 전략 실행, 설명 가능한 결과 저장.
6. 시간 순서 검증, T일 종가→T+1 체결 가정, 비용·거래정지·생존 편향 검토.
7. 장비 부하 측정, 재시작·정전·부분 수집 실패 복구와 백업 복원 검증.

종가만으로 다음 날 시가 체결을 검증할 수 없으므로 OHLC 수집을 확장한다.
현재 종목 목록과 사후 정정 데이터만 있으면 엄밀한 과거시점 재현에는 한계가 있다.
