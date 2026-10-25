"""Add source posts for the local test RSS channel.

Revision ID: 0003_local_test_rss
Revises: 0002_keyword_filters
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_local_test_rss"
down_revision = "0002_keyword_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_rss_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guid", sa.String(36), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("author", sa.String(255)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("test_rss_posts")
