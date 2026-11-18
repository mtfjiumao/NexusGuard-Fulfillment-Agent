"""Record customer requests and human resolutions on damage submissions."""

import sqlalchemy as sa
from alembic import op

revision = "0006_damage_application"
down_revision = "0005_damage_review"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("requested_resolution", sa.String(500)),
        sa.Column("outcome", sa.String(24)),
        sa.Column("resolution", sa.String(500)),
        sa.Column("resolution_reason", sa.String(500)),
        sa.Column("refund_cents", sa.BigInteger()),
        sa.Column("refund_currency", sa.String(3)),
        sa.Column("resolver_id", sa.String(128)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("damage_reviews", column)
    op.create_check_constraint(
        op.f("ck_damage_reviews_requested_resolution"),
        "damage_reviews",
        "requested_resolution IS NULL OR length(btrim(requested_resolution)) BETWEEN 1 AND 500",
    )
    op.create_check_constraint(
        op.f("ck_damage_reviews_resolution_fields"),
        "damage_reviews",
        "outcome IS NULL OR (outcome IN ('approved', 'needs_supplement', 'rejected')"
        " AND requested_resolution IS NOT NULL"
        " AND status IN ('passed', 'needs_supplement', 'rejected')"
        " AND resolved_at IS NOT NULL AND resolver_id IS NOT NULL"
        " AND resolution IS NOT NULL AND resolution_reason IS NOT NULL"
        " AND ((refund_cents IS NULL AND refund_currency IS NULL)"
        " OR (outcome = 'approved' AND refund_cents > 0 AND refund_currency IS NOT NULL"
        " AND refund_currency ~ '^[A-Z]{3}$')))",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM damage_reviews WHERE requested_resolution IS NOT NULL)"
        )
    ):
        raise RuntimeError("Archive damage applications before downgrading")
    for suffix in ("resolution_fields", "requested_resolution"):
        op.drop_constraint(op.f(f"ck_damage_reviews_{suffix}"), "damage_reviews", type_="check")
    for column in (
        "notified_at",
        "resolved_at",
        "resolver_id",
        "refund_currency",
        "refund_cents",
        "resolution_reason",
        "resolution",
        "outcome",
        "requested_resolution",
    ):
        op.drop_column("damage_reviews", column)
