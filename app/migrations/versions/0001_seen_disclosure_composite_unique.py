"""seen_disclosures: receipt_no 단독 unique를 (receipt_no, chat_id) 복합 unique로 교체

Revision ID: 0001_seen_dedup
Revises:
Create Date: 2026-07-21

배경: 발송 중복 기준은 사용자별인데 스키마는 receipt_no 단독 unique였다.
두 번째 사용자 삽입 시 UniqueViolation → 폴링 세션 전체 롤백 → 매 60초 재발송.

PostgreSQL 전용. 전체가 멱등이라 재실행·신규 DB(create_all이 이미 올바른
스키마를 만든 경우) 모두 안전하다. 기존 단독 unique 이름은 PostgreSQL 기본
명명 규칙(seen_disclosures_receipt_no_key)을 따른다 — create_all(unique=True)로
생성된 프로덕션/로컬 DB 공통.
"""
from alembic import op

revision = "0001_seen_dedup"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('seen_disclosures') IS NULL THEN
        RETURN;  -- 신규 DB: create_all이 이미 복합 제약으로 생성함
    END IF;

    -- 방어적 dedup (구 스키마에선 중복이 불가능하지만 안전장치)
    DELETE FROM seen_disclosures a
     USING seen_disclosures b
     WHERE a.receipt_no = b.receipt_no
       AND a.chat_id = b.chat_id
       AND a.ctid > b.ctid;

    ALTER TABLE seen_disclosures
        DROP CONSTRAINT IF EXISTS seen_disclosures_receipt_no_key;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_seen_disclosure_receipt_chat'
           AND conrelid = 'seen_disclosures'::regclass
    ) THEN
        ALTER TABLE seen_disclosures
            ADD CONSTRAINT uq_seen_disclosure_receipt_chat UNIQUE (receipt_no, chat_id);
    END IF;

    CREATE INDEX IF NOT EXISTS ix_seen_disclosures_receipt_no
        ON seen_disclosures (receipt_no);
END $$;
"""

DOWNGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('seen_disclosures') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE seen_disclosures
        DROP CONSTRAINT IF EXISTS uq_seen_disclosure_receipt_chat;

    DROP INDEX IF EXISTS ix_seen_disclosures_receipt_no;

    -- 주의: 사용자별 행이 이미 쌓였다면 단독 unique 복원은 실패할 수 있다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'seen_disclosures_receipt_no_key'
           AND conrelid = 'seen_disclosures'::regclass
    ) THEN
        ALTER TABLE seen_disclosures
            ADD CONSTRAINT seen_disclosures_receipt_no_key UNIQUE (receipt_no);
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
