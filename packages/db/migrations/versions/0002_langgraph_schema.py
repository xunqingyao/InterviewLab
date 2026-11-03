from alembic import op

revision = "0002_langgraph_schema"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph;")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE;")
