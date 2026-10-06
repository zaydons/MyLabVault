"""Add vitals table for patient measurements (weight, blood pressure, ...)

Revision ID: 005
Revises: 004
Create Date: 2026-10-06 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    """Create the vitals table unless it already exists (startup create_all may have made it)."""
    inspector = sa.inspect(op.get_bind())
    if 'vitals' in inspector.get_table_names():
        return
    op.create_table(
        'vitals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('vital_type', sa.String(length=30), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('value2', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('measured_at', sa.DateTime(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_vitals_id', 'vitals', ['id'])
    op.create_index('ix_vitals_patient_id', 'vitals', ['patient_id'])
    op.create_index('ix_vitals_vital_type', 'vitals', ['vital_type'])
    op.create_index('ix_vitals_measured_at', 'vitals', ['measured_at'])


def downgrade():
    op.drop_table('vitals')
