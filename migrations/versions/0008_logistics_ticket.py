"""Persist customer-confirmed logistics investigations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_logistics_ticket"
down_revision = "0007_damage_refund"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "logistics_tickets",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(128), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("result", sa.String(1000)),
        sa.Column("reviewer_id", sa.String(128)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["reception_sessions.tenant_id", "reception_sessions.id"]
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL AND reviewer_id IS NULL AND result IS NULL"
            " AND notified_at IS NULL)"
            " OR (resolved_at IS NOT NULL AND reviewer_id IS NOT NULL AND result IS NOT NULL"
            " AND length(btrim(result)) BETWEEN 1 AND 1000)",
            name="resolution_fields",
        ),
    )
    op.create_index(
        "uq_logistics_open_order",
        "logistics_tickets",
        ["tenant_id", "order_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_logistics_ticket_session",
        "logistics_tickets",
        ["tenant_id", "session_id", "created_at"],
    )
    op.create_index(
        "ix_logistics_ticket_queue", "logistics_tickets", ["tenant_id", "resolved_at", "created_at"]
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM logistics_tickets)")):
        raise RuntimeError("Archive logistics tickets before downgrading")
    op.drop_table("logistics_tickets")
