"""Add semantic filter configuration and persistent embedding caches.

Revision ID: 0004_semantic_filtering
Revises: 0003_local_test_rss
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_semantic_filtering"
down_revision = "0003_local_test_rss"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 1 already has a unique constraint on fingerprint; its extra plain
    # index duplicates the backing unique index and differed from metadata.
    op.drop_index("ix_articles_fingerprint", table_name="articles")
    op.add_column("filters", sa.Column("semantic_query", sa.Text(), nullable=True))
    op.add_column("filters", sa.Column("semantic_threshold", sa.Float(), nullable=True))
    op.create_table(
        "article_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("article_id", "model_name", name="uq_article_embedding_model"),
    )
    op.create_index("ix_article_embeddings_article_id", "article_embeddings", ["article_id"])
    op.create_table(
        "filter_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filter_id", sa.Integer(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("filter_id", "model_name", name="uq_filter_embedding_model"),
    )
    op.create_index("ix_filter_embeddings_filter_id", "filter_embeddings", ["filter_id"])


def downgrade() -> None:
    op.drop_index("ix_filter_embeddings_filter_id", table_name="filter_embeddings")
    op.drop_table("filter_embeddings")
    op.drop_index("ix_article_embeddings_article_id", table_name="article_embeddings")
    op.drop_table("article_embeddings")
    op.drop_column("filters", "semantic_threshold")
    op.drop_column("filters", "semantic_query")
    op.create_index("ix_articles_fingerprint", "articles", ["fingerprint"])
