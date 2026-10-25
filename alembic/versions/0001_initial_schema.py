"""Initial Phase 1 RSS aggregation schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(120), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("rss_feeds", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("url", sa.String(2048), nullable=False, unique=True), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("last_fetched_at", sa.DateTime(timezone=True)), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("feed_subscriptions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("feed_id", sa.Integer(), sa.ForeignKey("rss_feeds.id", ondelete="CASCADE"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("user_id", "feed_id", name="uq_subscription_user_feed"))
    op.create_table("articles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("feed_id", sa.Integer(), sa.ForeignKey("rss_feeds.id", ondelete="CASCADE"), nullable=False), sa.Column("external_id", sa.String(2048)), sa.Column("fingerprint", sa.String(64), nullable=False, unique=True), sa.Column("title", sa.Text(), nullable=False), sa.Column("url", sa.String(2048)), sa.Column("summary", sa.Text()), sa.Column("content", sa.Text()), sa.Column("author", sa.String(255)), sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_articles_feed_id", "articles", ["feed_id"])
    op.create_index("ix_articles_fingerprint", "articles", ["fingerprint"])
    op.create_index("ix_articles_published_at", "articles", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_index("ix_articles_fingerprint", table_name="articles")
    op.drop_index("ix_articles_feed_id", table_name="articles")
    op.drop_table("articles")
    op.drop_table("feed_subscriptions")
    op.drop_table("rss_feeds")
    op.drop_table("users")
