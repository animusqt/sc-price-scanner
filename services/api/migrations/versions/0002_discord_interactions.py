"""Track processed Discord interactions to prevent replay side effects.

Revision ID: 0002_discord_interactions
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_discord_interactions"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "discord_interactions" not in inspector.get_table_names():
        op.create_table(
            "discord_interactions",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_discord_interactions_received_at",
            "discord_interactions",
            ["received_at"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "discord_interactions" in inspector.get_table_names():
        op.drop_index(
            "ix_discord_interactions_received_at",
            table_name="discord_interactions",
        )
        op.drop_table("discord_interactions")
