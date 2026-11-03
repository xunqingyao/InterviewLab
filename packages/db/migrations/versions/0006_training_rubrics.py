"""Freeze question-specific rubrics for training and retests."""

from alembic import op

revision = "0006_training_rubrics"
down_revision = "0005_question_directions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE training_items ADD COLUMN IF NOT EXISTS rubric_snapshot_id UUID "
        "REFERENCES rubric_snapshots(id)"
    )


def downgrade() -> None:
    op.drop_column("training_items", "rubric_snapshot_id")
