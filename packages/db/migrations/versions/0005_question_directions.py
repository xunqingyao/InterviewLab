"""Persist selected question directions instead of inferring them from question wording."""

from alembic import op

revision = "0005_question_directions"
down_revision = "0004_training_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'ASK';")
    op.execute("ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS claim_id TEXT;")
    op.execute("ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS probe_key TEXT;")
    op.execute("UPDATE interview_turns SET action = 'FOLLOW_UP' WHERE followup_depth > 0;")


def downgrade() -> None:
    op.drop_column("interview_turns", "probe_key")
    op.drop_column("interview_turns", "claim_id")
    op.drop_column("interview_turns", "action")
