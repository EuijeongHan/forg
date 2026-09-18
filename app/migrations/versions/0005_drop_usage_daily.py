"""usage_daily 폐기 — usage_events로 대체

Revision ID: 0005_drop_usage_daily
Revises: 0004_feedback_status
Create Date: 2026-09-18

배경: usage_daily는 (날짜, 이벤트) 카운터만 담고 기업·공시번호·검색어를 버리도록
설계됐다(#82). 사용자가 늘어도 평가와 품질 개선에 쓸 수 있는 것이 쌓이지 않는
설계였다 — 어떤 검색어가 0건을 내는지, 사용자가 연 요약이 제대로 나왔는지가
전부 목적어 쪽에 있다. 같은 날 usage_events(행 단위)로 교체한다.

배포 후 수 시간만 존재했으므로 담긴 것이 거의 없다. usage_events는 create_all이
만든다. PostgreSQL 전용, 멱등.
"""
from alembic import op

revision = "0005_drop_usage_daily"
down_revision = "0004_feedback_status"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE IF EXISTS usage_daily")


def downgrade():
    # 되살리지 않는다 — 폐기된 설계다. usage_events는 그대로 남는다.
    pass
