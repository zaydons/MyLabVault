"""Add diagnoses table (conditions per patient)

Revision ID: 009
Revises: 008
Create Date: 2026-11-07 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    """Create the diagnoses table unless it already exists (startup create_all may have made it)."""
    inspector = sa.inspect(op.get_bind())
    if 'diagnoses' in inspector.get_table_names():
        return
    op.create_table(
        'diagnoses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('icd10_code', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('onset_date', sa.Date(), nullable=True),
        sa.Column('resolved_date', sa.Date(), nullable=True),
        sa.Column('provider_id', sa.Integer(), sa.ForeignKey('providers.id'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_diagnoses_id', 'diagnoses', ['id'])
    op.create_index('ix_diagnoses_patient_id', 'diagnoses', ['patient_id'])
    op.create_index('ix_diagnoses_name', 'diagnoses', ['name'])


def downgrade():
    op.drop_table('diagnoses')
