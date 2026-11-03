from alembic import op

revision = "0003_retrieval_and_parse"
down_revision = "0002_langgraph_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("ALTER TABLE resumes ADD COLUMN IF NOT EXISTS parse_error TEXT;")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_chunks'
                  AND column_name = 'tsv'
                  AND udt_name = 'text'
            ) THEN
                ALTER TABLE knowledge_chunks
                ALTER COLUMN tsv TYPE tsvector
                USING CASE
                    WHEN tsv IS NULL OR btrim(tsv) = '' THEN NULL
                    ELSE to_tsvector('simple', tsv)
                END;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_embeddings'
                  AND column_name = 'vector'
                  AND udt_name = 'jsonb'
            ) THEN
                ALTER TABLE knowledge_embeddings
                ALTER COLUMN vector TYPE vector(32)
                USING trim(both '"' from vector::text)::vector;
            END IF;
        END $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_tsv ON knowledge_chunks USING gin (tsv);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_version ON knowledge_chunks (version_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_tsv;")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_version;")
    op.execute("ALTER TABLE resumes DROP COLUMN IF EXISTS parse_error;")
