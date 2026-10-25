"""Add Phase 2 keyword filters and explainable article matches.

Revision ID: 0002_keyword_filters
Revises: 0001_initial_schema
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_keyword_filters"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("filter_type", sa.String(32), nullable=False, server_default="keyword"),
        sa.Column("match_mode", sa.String(16), nullable=False, server_default="ANY"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_filters_user_id", "filters", ["user_id"])
    op.create_table(
        "filter_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filter_id", sa.Integer(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword", sa.String(255), nullable=False),
        sa.Column("normalized_keyword", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("filter_id", "normalized_keyword", name="uq_filter_keyword_normalized"),
    )
    op.create_table(
        "article_filter_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filter_id", sa.Integer(), sa.ForeignKey("filters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("matched_keywords", sa.JSON(), nullable=False),
        sa.Column("matched_fields", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("article_id", "filter_id", name="uq_article_filter_match"),
    )
    op.create_index("ix_article_filter_matches_article_id", "article_filter_matches", ["article_id"])
    op.create_index("ix_article_filter_matches_filter_id", "article_filter_matches", ["filter_id"])


def downgrade() -> None:
    op.drop_index("ix_article_filter_matches_filter_id", table_name="article_filter_matches")
    op.drop_index("ix_article_filter_matches_article_id", table_name="article_filter_matches")
    op.drop_table("article_filter_matches")
    op.drop_table("filter_keywords")
    op.drop_index("ix_filters_user_id", table_name="filters")
    op.drop_table("filters")
