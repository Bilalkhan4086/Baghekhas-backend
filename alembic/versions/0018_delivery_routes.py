"""Add persistent rider routes, stops, refresh sessions, and action receipts.

Revision ID: 0018_delivery_routes
Revises: 0017_order_delivery_time
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_delivery_routes"
down_revision: str | None = "0017_order_delivery_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("start_latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("start_longitude", sa.Numeric(10, 6), nullable=False),
        sa.Column("start_source", sa.String(length=20), nullable=False),
        sa.Column("total_distance_meters", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('generated', 'in_progress', 'completed', 'cancelled')",
            name="delivery_routes_status_valid",
        ),
        sa.CheckConstraint(
            "start_latitude BETWEEN -90 AND 90",
            name="delivery_routes_start_latitude_valid",
        ),
        sa.CheckConstraint(
            "start_longitude BETWEEN -180 AND 180",
            name="delivery_routes_start_longitude_valid",
        ),
        sa.CheckConstraint(
            "start_source IN ('gps', 'depot')",
            name="delivery_routes_start_source_valid",
        ),
        sa.CheckConstraint(
            "total_distance_meters >= 0",
            name="delivery_routes_distance_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_duration_seconds >= 0",
            name="delivery_routes_duration_nonnegative",
        ),
        sa.ForeignKeyConstraint(["rider_id"], ["riders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "delivery_routes_rider_date_active_uidx",
        "delivery_routes",
        ["rider_id", "delivery_date"],
        unique=True,
        postgresql_where=sa.text("status IN ('generated', 'in_progress')"),
    )
    op.create_index(
        "delivery_routes_date_status_idx",
        "delivery_routes",
        ["delivery_date", "status"],
    )

    op.create_table(
        "route_stops",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("distance_from_previous_meters", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("not_received_reason", sa.String(length=40)),
        sa.Column("outcome_note", sa.String(length=500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sequence > 0", name="route_stops_sequence_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'in_progress', 'delivered', "
            "'not_received', 'cancelled')",
            name="route_stops_status_valid",
        ),
        sa.CheckConstraint(
            "distance_from_previous_meters >= 0",
            name="route_stops_distance_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_duration_seconds >= 0",
            name="route_stops_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "not_received_reason IS NULL OR not_received_reason IN "
            "('customer_unavailable', 'customer_refused', 'wrong_address', "
            "'phone_unreachable', 'requested_later', 'other')",
            name="route_stops_not_received_reason_valid",
        ),
        sa.CheckConstraint(
            "not_received_reason <> 'other' OR "
            "(outcome_note IS NOT NULL AND length(trim(outcome_note)) > 0)",
            name="route_stops_other_note_required",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["route_id"], ["delivery_routes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_id", "order_id", name="route_stops_route_order_key"),
        sa.UniqueConstraint("route_id", "sequence", name="route_stops_route_sequence_key"),
    )
    op.create_index(
        "route_stops_route_current_uidx",
        "route_stops",
        ["route_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ready', 'in_progress')"),
    )
    op.create_index(
        "route_stops_order_unresolved_uidx",
        "route_stops",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'ready', 'in_progress')"),
    )
    op.create_index(
        "route_stops_route_status_sequence_idx",
        "route_stops",
        ["route_id", "status", "sequence"],
    )

    op.create_table(
        "rider_refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["rider_id"], ["riders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "rider_refresh_sessions_rider_expires_idx",
        "rider_refresh_sessions",
        ["rider_id", "expires_at"],
    )

    op.create_table(
        "rider_action_receipts",
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("rider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["rider_id"], ["riders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["route_id"], ["delivery_routes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stop_id"], ["route_stops.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_index(
        "rider_action_receipts_route_created_idx",
        "rider_action_receipts",
        ["route_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "0018_delivery_routes is forward-only because route, outcome, and rider-session "
        "history must not be discarded."
    )
