"""feedback: 처리 상태(status) 컬럼 추가

Revision ID: 0004_feedback_status
Revises: 0003_watchlist_unique
Create Date: 2026-08-26

배경: 사용자 요청·신고는 1순위이고 놓쳐선 안 되는데, feedback 테이블에는 접수만
쌓이고 처리 여부가 없었다. 대화가 넘어가면 조용히 묻힌다 — 이 서비스가 계속
없애온 '침묵 실패'와 같은 형태다. status로 미처리를 드러내고, 일일 리마인더가
남은 건을 운영자에게 계속 알린다.

PostgreSQL 전용. 멱등이라 재실행·신규 DB 모두 안전하다.
"""
from alembic import op

revision = "0004_feedback_status"
down_revision = "0003_watchlist_unique"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('feedback') IS NULL THEN
        RETURN;  -- 신규 DB: create_all이 컬럼과 함께 생성함
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'feedback' AND column_name = 'status'
    ) THEN
        ALTER TABLE feedback ADD COLUMN status VARCHAR NOT NULL DEFAULT 'new';
    END IF;

    -- 기존 행은 전부 미처리로 남긴다. 이미 대응한 건이라도 운영자가 직접
    -- 완료 표시하게 두는 편이, 처리되지 않은 것을 처리된 것으로 만드는 것보다 낫다.
END $$;
"""

DOWNGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('feedback') IS NOT NULL THEN
        ALTER TABLE feedback DROP COLUMN IF EXISTS status;
    END IF;
END $$;
"""


def upgrade():
    op.execute(UPGRADE_SQL)


def downgrade():
    op.execute(DOWNGRADE_SQL)
