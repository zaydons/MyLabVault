"""Add per-result reference range, lab flag, lab comment and fasting status

Revision ID: 004
Revises: 003
Create Date: 2026-10-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    sa.Column('ref_low', sa.Float(), nullable=True),
    sa.Column('ref_high', sa.Float(), nullable=True),
    sa.Column('ref_text', sa.String(length=100), nullable=True),
    sa.Column('flag', sa.String(length=20), nullable=True),
    sa.Column('lab_comment', sa.Text(), nullable=True),
    sa.Column('fasting', sa.Boolean(), nullable=True),
]


def upgrade():
    """Add the columns that are missing (tables may already have them via create_all)."""
    inspector = sa.inspect(op.get_bind())
    existing = {col['name'] for col in inspector.get_columns('lab_results')}
    for column in NEW_COLUMNS:
        if column.name not in existing:
            op.add_column('lab_results', column.copy())


def downgrade():
    """Drop the per-result report detail columns."""
    with op.batch_alter_table('lab_results') as batch_op:
        for column in reversed(NEW_COLUMNS):
            batch_op.drop_column(column.name)
