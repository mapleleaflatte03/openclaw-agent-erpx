"""Add audit_log immutability trigger (Postgres only)

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-07

Creates a Postgres trigger that prevents UPDATE and DELETE on agent_audit_log.
The trigger is only created on PostgreSQL; SQLite is skipped silently.
"""
from alembic import op

revision = "0004_audit_immutability_trigger"
down_revision = "0003_add_risk_and_approval_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect != "postgresql":
        # SQLite does not support triggers in the same way; skip.
        return

    # Create function that raises exception on mutate
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % not allowed', TG_OP;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Attach trigger for UPDATE
    op.execute("""
        DROP TRIGGER IF EXISTS trg_audit_no_update ON agent_audit_log;
        CREATE TRIGGER trg_audit_no_update
        BEFORE UPDATE ON agent_audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
    """)

    # Attach trigger for DELETE
    op.execute("""
        DROP TRIGGER IF EXISTS trg_audit_no_delete ON agent_audit_log;
        CREATE TRIGGER trg_audit_no_delete
        BEFORE DELETE ON agent_audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
    """)


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect != "postgresql":
        return

    op.execute("DROP TRIGGER IF EXISTS trg_audit_no_update ON agent_audit_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_no_delete ON agent_audit_log;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation();")
