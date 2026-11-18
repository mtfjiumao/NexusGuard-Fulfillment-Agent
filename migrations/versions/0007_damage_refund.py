"""Link reviewed damage applications to their financial workflow."""

import sqlalchemy as sa
from alembic import op

revision = "0007_damage_refund"
down_revision = "0006_damage_application"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("damage_reviews", sa.Column("refund_case_id", sa.Uuid()))
    op.create_foreign_key(
        op.f("fk_damage_reviews_tenant_id_aftersales_cases"),
        "damage_reviews",
        "aftersales_cases",
        ["tenant_id", "refund_case_id"],
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        op.f("uq_damage_reviews_tenant_id_refund_case_id"),
        "damage_reviews",
        ["tenant_id", "refund_case_id"],
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM damage_reviews WHERE refund_case_id IS NOT NULL)")
    ):
        raise RuntimeError("Archive linked refund workflows before downgrading")
    op.drop_constraint(
        op.f("uq_damage_reviews_tenant_id_refund_case_id"), "damage_reviews", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_damage_reviews_tenant_id_aftersales_cases"), "damage_reviews", type_="foreignkey"
    )
    op.drop_column("damage_reviews", "refund_case_id")
