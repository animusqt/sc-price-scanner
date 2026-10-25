"""Remove the retired Discord integration and stored identity.

Revision ID: 0003_remove_discord_identity
Revises: 0002_discord_interactions
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_remove_discord_identity"
down_revision = "0002_discord_interactions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "discord_interactions" in inspector.get_table_names():
        op.drop_table("discord_interactions")
    if "users" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("users")}
        if "discord_id" in columns:
            indexes = {index["name"] for index in inspector.get_indexes("users")}
            if "ix_users_discord_id" in indexes:
                op.drop_index("ix_users_discord_id", table_name="users")
            with op.batch_alter_table("users") as batch_op:
                batch_op.drop_column("discord_id")


def downgrade():
    raise RuntimeError("Removed Discord identities are intentionally not recoverable.")
