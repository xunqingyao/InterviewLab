from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    from db.models import Base

    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_open_turn_per_session
        ON interview_turns (session_id)
        WHERE status IN ('RESERVED', 'READY');
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_final_answer_per_turn
        ON messages (turn_id)
        WHERE kind = 'ANSWER';
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_question_per_turn
        ON messages (turn_id)
        WHERE kind = 'QUESTION';
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_job_dedupe ON jobs (dedupe_key);")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_ready
        ON jobs (priority DESC, available_at, id)
        WHERE status IN ('QUEUED', 'RETRY_WAIT');
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_expired_lease
        ON jobs (lease_expires_at)
        WHERE status = 'RUNNING';
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    from db.models import Base

    Base.metadata.drop_all(bind=bind)
