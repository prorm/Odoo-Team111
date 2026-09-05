"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-08-06 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Enable btree_gist extension for range overlap exclusion constraint
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gist";')

    # 2. Create distinct Postgres roles for migration owner vs runtime app
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'harmonix360_admin') THEN
        CREATE ROLE harmonix360_admin WITH LOGIN PASSWORD 'admin_password' SUPERUSER;
      END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'harmonix360_app') THEN
        CREATE ROLE harmonix360_app WITH LOGIN PASSWORD 'app_password';
      END IF;
    END
    $$;
    """)

    # 3. Create Departments table
    op.create_table(
        'departments',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('head_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='ACTIVE'),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', 'tenant_id', name='uq_department_code_tenant')
    )
    op.create_index('ix_departments_public_id', 'departments', ['public_id'], unique=True)

    # 4. Create Users table
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False, server_default='EMPLOYEE'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='ACTIVE'),
        sa.Column('department_id', sa.BigInteger(), sa.ForeignKey('departments.id'), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_public_id', 'users', ['public_id'], unique=True)
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # Foreign key for department head
    op.create_foreign_key('fk_departments_head_user', 'departments', 'users', ['head_id'], ['id'])

    # 5. Create Asset Categories table
    op.create_table(
        'asset_categories',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_asset_categories_public_id', 'asset_categories', ['public_id'], unique=True)

    # 6. Create Assets table
    op.create_table(
        'assets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('asset_tag', sa.String(length=128), nullable=False),
        sa.Column('serial_number', sa.String(length=128), nullable=True),
        sa.Column('category_id', sa.BigInteger(), sa.ForeignKey('asset_categories.id'), nullable=False),
        sa.Column('department_id', sa.BigInteger(), sa.ForeignKey('departments.id'), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='AVAILABLE'),
        sa.Column('condition', sa.String(length=32), nullable=False, server_default='GOOD'),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('is_bookable', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('purchase_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('purchase_cost', sa.Float(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('asset_tag', 'tenant_id', name='uq_asset_tag_tenant')
    )
    op.create_index('ix_assets_public_id', 'assets', ['public_id'], unique=True)
    op.create_index('ix_assets_status', 'assets', ['status'])

    # 7. Create Allocations table
    op.create_table(
        'allocations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('allocated_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('allocated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('returned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('return_condition', sa.String(length=32), nullable=True),
        sa.Column('check_in_notes', sa.Text(), nullable=True),
        sa.Column('is_overdue', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('overdue_notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='ACTIVE'),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_allocations_public_id', 'allocations', ['public_id'], unique=True)

    # 8. Create Resource Bookings table
    op.create_table(
        'resource_bookings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('purpose', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_resource_bookings_public_id', 'resource_bookings', ['public_id'], unique=True)

    # 9. Add raw SQL EXCLUDE constraint on resource_bookings using btree_gist and tstzrange
    op.execute("""
    ALTER TABLE resource_bookings ADD CONSTRAINT resource_bookings_range_overlap_excl EXCLUDE USING gist (
      asset_id WITH =,
      tstzrange(start_time, end_time) WITH &&
    );
    """)

    # 10. Create Transfer Requests table
    op.create_table(
        'transfer_requests',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('from_user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('to_user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('requested_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('approved_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )

    # 11. Create Maintenance Requests table
    op.create_table(
        'maintenance_requests',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('requested_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=32), nullable=False, server_default='MEDIUM'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('approved_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )

    # 12. Create Audit Cycles table
    op.create_table(
        'audit_cycles',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('scope_dept_id', sa.BigInteger(), sa.ForeignKey('departments.id'), nullable=True),
        sa.Column('scope_location', sa.String(length=255), nullable=True),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PLANNED'),
        sa.Column('auditor_ids', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )

    # 13. Create Audit Asset Logs table
    op.create_table(
        'audit_asset_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('audit_cycle_id', sa.BigInteger(), sa.ForeignKey('audit_cycles.id'), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('expected_location', sa.String(length=255), nullable=True),
        sa.Column('actual_location', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('verified_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )

    # 14. Create Discrepancies table
    op.create_table(
        'discrepancies',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('audit_cycle_id', sa.BigInteger(), sa.ForeignKey('audit_cycles.id'), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='OPEN'),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('resolved_by', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )

    # 15. Create Notifications table
    op.create_table(
        'notifications',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('type', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )

    # 16. Create Activity Logs table
    op.create_table(
        'activity_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(length=128), nullable=False),
        sa.Column('entity_type', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=128), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )

    # 17. Create Audit Logs table (Immutable mutation audit log)
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('actor', sa.String(length=128), nullable=False),
        sa.Column('action', sa.String(length=128), nullable=False),
        sa.Column('entity', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=128), nullable=False),
        sa.Column('before_diff', sa.JSON(), nullable=True),
        sa.Column('after_diff', sa.JSON(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(length=128), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_public_id', 'audit_logs', ['public_id'], unique=True)
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])

    # 18. Grant permissions & enforce Immutability on audit_logs for harmonix360_app role
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO harmonix360_admin;")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO harmonix360_admin;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO harmonix360_app;")
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM harmonix360_app;")
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC;")

    # 19. Create Audit Events table
    op.create_table(
        'audit_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('entity', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=128), nullable=False),
        sa.Column('action', sa.String(length=128), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('actor_type', sa.String(length=32), nullable=False, server_default='HUMAN'),
        sa.Column('actor_id', sa.String(length=128), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('request_id', sa.String(length=128), nullable=True),
        sa.Column('correlation_id', sa.String(length=128), nullable=True),
        sa.Column('trace_id', sa.String(length=128), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('audit_events')
    op.drop_table('audit_logs')
    op.drop_table('activity_logs')
    op.drop_table('notifications')
    op.drop_table('discrepancies')
    op.drop_table('audit_asset_logs')
    op.drop_table('audit_cycles')
    op.drop_table('maintenance_requests')
    op.drop_table('transfer_requests')
    op.drop_table('resource_bookings')
    op.drop_table('allocations')
    op.drop_table('assets')
    op.drop_table('asset_categories')
    op.drop_table('users')
    op.drop_table('departments')
