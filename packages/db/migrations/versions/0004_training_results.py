from alembic import op

revision = "0004_training_results"
down_revision = "0003_retrieval_and_parse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exercise_submissions ADD COLUMN IF NOT EXISTS result_json JSONB NOT NULL DEFAULT '{}'::jsonb;")


def downgrade() -> None:
    op.execute("ALTER TABLE exercise_submissions DROP COLUMN IF EXISTS result_json;")
