"""002_week2_transfer_ai

Revision ID: 002_week2_transfer_ai
Revises: 001_initial_schema
Create Date: 2026-08-06 20:00:00.000000

Adds ai_decision_data JSONB column to transfer_requests
and indexes for transfer_requests.public_id and status.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_week2_transfer_ai'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add ai_decision_data JSONB column to transfer_requests
    op.add_column(
        'transfer_requests',
        sa.Column('ai_decision_data', sa.JSON(), nullable=True)
    )

    # Add indexes for transfer_requests (were missing in initial migration)
    op.create_index('ix_transfer_requests_public_id', 'transfer_requests', ['public_id'], unique=True)
    op.create_index('ix_transfer_requests_status', 'transfer_requests', ['status'])

    # Grant permissions on any new sequences/tables to harmonix360_app
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO harmonix360_app;")

    # Re-enforce immutability on audit_logs (in case grants above broadened it)
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM harmonix360_app;")
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC;")


def downgrade() -> None:
    op.drop_index('ix_transfer_requests_status', 'transfer_requests')
    op.drop_index('ix_transfer_requests_public_id', 'transfer_requests')
    op.drop_column('transfer_requests', 'ai_decision_data')
