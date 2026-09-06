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
