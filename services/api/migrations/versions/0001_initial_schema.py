"""Create the initial SCIP v2 data model.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op

from scip_api.database import Base
from scip_api import models  # noqa: F401

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
