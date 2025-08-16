"""Remove provider_name column from pdf_import_logs

Revision ID: 003
Revises: 002
Create Date: 2025-01-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    """Remove provider_name column since we now use provider relationship."""
    # Check if the column exists before trying to drop it
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('pdf_import_logs')]
    
    if 'provider_name' in columns:
        # Remove the provider_name column from pdf_import_logs table
        op.drop_column('pdf_import_logs', 'provider_name')
    else:
        # Column doesn't exist, migration already applied or column was never created
        pass


def downgrade():
    """Add back provider_name column if needed."""
    # Add back the provider_name column
    op.add_column('pdf_import_logs', sa.Column('provider_name', sa.String(length=255), nullable=True))