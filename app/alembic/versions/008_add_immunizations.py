"""Add immunizations table (vaccine doses per patient)

Revision ID: 008
Revises: 007
Create Date: 2026-10-09 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade():
    """Create the immunizations table unless it already exists (startup create_all may have made it)."""
    inspector = sa.inspect(op.get_bind())
    if 'immunizations' in inspector.get_table_names():
        return
    op.create_table(
        'immunizations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('vaccine', sa.String(length=255), nullable=False),
        sa.Column('date_given', sa.Date(), nullable=False),
        sa.Column('dose', sa.String(length=50), nullable=True),
        sa.Column('manufacturer', sa.String(length=100), nullable=True),
        sa.Column('lot_number', sa.String(length=50), nullable=True),
        sa.Column('site', sa.String(length=50), nullable=True),
        sa.Column('provider_id', sa.Integer(), sa.ForeignKey('providers.id'), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('next_due', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_immunizations_id', 'immunizations', ['id'])
    op.create_index('ix_immunizations_patient_id', 'immunizations', ['patient_id'])
    op.create_index('ix_immunizations_vaccine', 'immunizations', ['vaccine'])
    op.create_index('ix_immunizations_date_given', 'immunizations', ['date_given'])


def downgrade():
    op.drop_table('immunizations')
