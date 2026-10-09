"""Add medications table (medications and supplements per patient, one row per dose period)

Revision ID: 007
Revises: 006
Create Date: 2026-10-09 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    """Create the medications table unless it already exists (startup create_all may have made it)."""
    inspector = sa.inspect(op.get_bind())
    if 'medications' in inspector.get_table_names():
        return
    op.create_table(
        'medications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='medication'),
        sa.Column('dose', sa.String(length=100), nullable=True),
        sa.Column('frequency', sa.String(length=100), nullable=True),
        sa.Column('route', sa.String(length=50), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('provider_id', sa.Integer(), sa.ForeignKey('providers.id'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_medications_id', 'medications', ['id'])
    op.create_index('ix_medications_patient_id', 'medications', ['patient_id'])
    op.create_index('ix_medications_name', 'medications', ['name'])
    op.create_index('ix_medications_start_date', 'medications', ['start_date'])


def downgrade():
    op.drop_table('medications')
