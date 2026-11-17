"""Persist customer reception sessions and deduplicated questions.

Revision ID: 0002_reception
Revises: 0001_core
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_reception"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def identity_columns():
    # Frozen migration helpers; do not import evolving application models here.
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade():
    op.create_unique_constraint("uq_order_customer_scope", "orders", ["tenant_id", "id", "user_id"])
    op.create_table(
        "reception_sessions",
        *identity_columns(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("entry_point", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("candidate_order_id", sa.Uuid(), nullable=True),
        sa.Column("bound_order_id", sa.Uuid(), nullable=True),
        sa.Column("sku_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_reception_sessions"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_reception_sessions_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_reception_sessions_tenant_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
            name="fk_reception_candidate_owner",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bound_order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
            name="fk_reception_bound_owner",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sku_id"],
            ["skus.tenant_id", "skus.id"],
            name="fk_reception_sessions_tenant_id_skus",
        ),
        sa.CheckConstraint(
            "entry_point IN ('order', 'store', 'other')",
            name=op.f("ck_reception_sessions_entry_point"),
        ),
        sa.CheckConstraint(
            "state IN ('confirming', 'alternatives', 'selecting', "
            "'general', 'bound', 'clarifying')",
            name=op.f("ck_reception_sessions_reception_state"),
        ),
        sa.CheckConstraint(
            "(state = 'confirming' AND candidate_order_id IS NOT NULL "
            "AND bound_order_id IS NULL) OR "
            "(state = 'bound' AND bound_order_id IS NOT NULL AND candidate_order_id IS NULL) OR "
            "(state IN ('alternatives', 'selecting', 'general', 'clarifying') "
            "AND candidate_order_id IS NULL AND bound_order_id IS NULL)",
            name=op.f("ck_reception_sessions_order_context"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_reception_sessions_positive_version")),
    )
    op.create_index(
        "ix_reception_user", "reception_sessions", ["tenant_id", "user_id", "created_at"]
    )
    op.create_table(
        "reception_messages",
        *identity_columns(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_reception_messages"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_reception_messages_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["reception_sessions.tenant_id", "reception_sessions.id"],
            name="fk_reception_messages_tenant_id_reception_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_reception_messages_tenant_id_orders",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) BETWEEN 1 AND 4000",
            name=op.f("ck_reception_messages_message_length"),
        ),
        sa.CheckConstraint(
            "status IN ('needs_order', 'ready_for_agent', 'superseded')",
            name=op.f("ck_reception_messages_message_status"),
        ),
        sa.CheckConstraint(
            "status != 'needs_order' OR order_id IS NULL",
            name=op.f("ck_reception_messages_unresolved_order"),
        ),
    )
    op.create_index(
        "ix_reception_messages_session",
        "reception_messages",
        ["tenant_id", "session_id", "created_at"],
    )
    op.create_index(
        "uq_reception_pending_message",
        "reception_messages",
        ["tenant_id", "session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'needs_order'"),
    )


def downgrade():
    op.drop_table("reception_messages")
    op.drop_table("reception_sessions")
    op.drop_constraint("uq_order_customer_scope", "orders", type_="unique")
