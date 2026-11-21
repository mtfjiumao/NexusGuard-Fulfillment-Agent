"""Extend logistics tickets with reviewed delay compensation."""

import sqlalchemy as sa
from alembic import op

revision = "0009_delay_compensation"
down_revision = "0008_logistics_ticket"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "logistics_tickets",
        sa.Column("kind", sa.String(24), nullable=False, server_default="investigation"),
    )
    op.add_column("logistics_tickets", sa.Column("refund_case_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_logistics_tickets_tenant_id_aftersales_cases",
        "logistics_tickets",
        "aftersales_cases",
        ["tenant_id", "refund_case_id"],
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_logistics_tickets_tenant_id_refund_case_id",
        "logistics_tickets",
        ["tenant_id", "refund_case_id"],
    )
    op.create_check_constraint(
        op.f("ck_logistics_tickets_compensation_fields"),
        "logistics_tickets",
        "kind IN ('investigation', 'delay_compensation')"
        " AND (refund_case_id IS NULL"
        " OR (kind = 'delay_compensation' AND resolved_at IS NOT NULL))",
    )
    op.drop_index("uq_logistics_open_order", table_name="logistics_tickets")
    op.create_index(
        "uq_logistics_open_order_kind",
        "logistics_tickets",
        ["tenant_id", "order_id", "kind"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM logistics_tickets WHERE kind = 'delay_compensation')")
    ):
        raise RuntimeError("Archive delay compensation applications before downgrading")
    op.drop_index("uq_logistics_open_order_kind", table_name="logistics_tickets")
    op.create_index(
        "uq_logistics_open_order",
        "logistics_tickets",
        ["tenant_id", "order_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_constraint(
        op.f("ck_logistics_tickets_compensation_fields"), "logistics_tickets", type_="check"
    )
    op.drop_constraint(
        "uq_logistics_tickets_tenant_id_refund_case_id", "logistics_tickets", type_="unique"
    )
    op.drop_constraint(
        "fk_logistics_tickets_tenant_id_aftersales_cases", "logistics_tickets", type_="foreignkey"
    )
    op.drop_column("logistics_tickets", "refund_case_id")
    op.drop_column("logistics_tickets", "kind")
