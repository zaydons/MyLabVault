"""Add a plain-language description to lab tests

Revision ID: 006
Revises: 005
Create Date: 2026-10-07 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade():
    """Add labs.description unless it already exists (startup create_all may have made it)."""
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('labs')}
    if 'description' not in columns:
        with op.batch_alter_table('labs') as batch_op:
            batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('labs') as batch_op:
        batch_op.drop_column('description')
