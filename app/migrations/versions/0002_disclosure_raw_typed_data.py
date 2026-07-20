"""disclosures: raw_typed_data JSON 컬럼 추가 (정형 응답 스냅샷, P1-2)

Revision ID: 0002_raw_typed
Revises: 0001_seen_dedup
Create Date: 2026-07-21

additive-only: 구버전 코드와 공존 가능(컬럼 무시됨). PostgreSQL 전용·멱등.
신규 DB는 create_all이 컬럼 포함 스키마를 만들므로 no-op.
"""
from alembic import op

revision = "0002_raw_typed"
down_revision = "0001_seen_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF to_regclass('disclosures') IS NULL THEN
        RETURN;
    END IF;
    ALTER TABLE disclosures ADD COLUMN IF NOT EXISTS raw_typed_data JSON;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF to_regclass('disclosures') IS NULL THEN
        RETURN;
    END IF;
    ALTER TABLE disclosures DROP COLUMN IF EXISTS raw_typed_data;
END $$;
""")
