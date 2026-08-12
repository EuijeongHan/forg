"""watchlist: (chat_id, corp_code) 복합 unique 추가

Revision ID: 0003_watchlist_unique
Revises: 0002_raw_typed
Create Date: 2026-08-12

배경: 중복 등록 방지가 watchlist_service의 사전 조회에만 의존했다. 조회와 삽입
사이에 같은 사용자의 요청이 겹치면 중복 행이 남고, 해당 기업 공시는 알림이 두 번
나간다(SeenDisclosure는 (receipt_no, chat_id) 기준이라 워치리스트 중복을 막지 못한다).

PostgreSQL 전용. 멱등이라 재실행·신규 DB 모두 안전하다. 제약 추가 전에 중복 행을
정리하며, 가장 먼저 등록된 행(ctid 최소)을 남긴다.
"""
from alembic import op

revision = "0003_watchlist_unique"
down_revision = "0002_raw_typed"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('watchlist') IS NULL THEN
        RETURN;  -- 신규 DB: create_all이 이미 제약과 함께 생성함
    END IF;

    -- 제약을 걸기 전에 기존 중복을 정리한다(먼저 등록된 행 보존)
    DELETE FROM watchlist a
     USING watchlist b
     WHERE a.chat_id = b.chat_id
       AND a.corp_code = b.corp_code
       AND a.ctid > b.ctid;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_watchlist_chat_corp'
           AND conrelid = 'watchlist'::regclass
    ) THEN
        ALTER TABLE watchlist
            ADD CONSTRAINT uq_watchlist_chat_corp UNIQUE (chat_id, corp_code);
    END IF;
END $$;
"""

DOWNGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('watchlist') IS NOT NULL THEN
        ALTER TABLE watchlist
            DROP CONSTRAINT IF EXISTS uq_watchlist_chat_corp;
    END IF;
END $$;
"""


def upgrade():
    op.execute(UPGRADE_SQL)


def downgrade():
    op.execute(DOWNGRADE_SQL)
