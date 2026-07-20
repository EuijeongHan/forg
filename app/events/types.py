"""이벤트 타입 상수 — DB(event_type 컬럼)와 renderer 분기의 단일 어휘."""

EVENT_CONVERTIBLE_BOND = "convertible_bond"
EVENT_PAID_INCREASE = "paid_in_capital_increase"
EVENT_OTHER = "other"

# 이후 유형은 설문(Stage 1) 결과에 따라 추가한다 — 계획서 §3.7 권장 순서:
# 자기주식 → 감자 → 합병·분할 → BW·교환사채 → 무상증자
