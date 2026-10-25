"""Add extensible commodity, ship and fleet catalogs."""

from alembic import op
import sqlalchemy as sa

revision = "0004_catalog_management"
down_revision = "0003_remove_discord_identity"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column(table_name, column):
    if table_name in _tables() and column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_table_if_missing(name, *elements, **kwargs):
    if name not in _tables():
        op.create_table(name, *elements, **kwargs)


def _create_index_if_missing(name, table_name, columns):
    if name not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}:
        op.create_index(name, table_name, columns)


def _drop_table_if_present(name):
    if name in _tables():
        op.drop_table(name)


def _drop_column_if_present(table_name, column_name):
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def _drop_index_if_present(table_name, index_name):
    if table_name in _tables() and index_name in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }:
        op.drop_index(index_name, table_name=table_name)


def upgrade():
    _add_column("commodities", sa.Column("category", sa.String(100), nullable=True))
    _add_column("commodities", sa.Column("description", sa.Text(), nullable=True))
    _add_column("commodities", sa.Column("legal_status", sa.String(40), nullable=True))
    _add_column("commodities", sa.Column("base_price", sa.Integer(), nullable=True))
    _add_column(
        "commodities",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "commodities",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    if "commodities" in _tables():
        _create_index_if_missing("ix_commodities_category", "commodities", ["category"])
    _add_column("locations", sa.Column("planet", sa.String(100), nullable=True))
    _add_column("locations", sa.Column("moon", sa.String(100), nullable=True))
    _add_column(
        "source_ingestions",
        sa.Column("result_json", sa.JSON(), nullable=False, server_default="{}"),
    )

    _create_table_if_missing(
        "commodity_prices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("commodity_id", sa.String(36), sa.ForeignKey("commodities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("buy_price", sa.Integer(), nullable=True),
        sa.Column("sell_price", sa.Integer(), nullable=True),
        sa.Column("supply", sa.Integer(), nullable=True),
        sa.Column("demand", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("patch_version", sa.String(80), nullable=False, server_default="live"),
        sa.Column("server_shard", sa.String(100), nullable=False, server_default="global"),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("external_id", sa.String(180), nullable=True),
        sa.CheckConstraint("buy_price IS NULL OR buy_price >= 0", name="ck_catalog_buy_nonnegative"),
        sa.CheckConstraint("sell_price IS NULL OR sell_price >= 0", name="ck_catalog_sell_nonnegative"),
        sa.CheckConstraint("supply IS NULL OR supply >= 0", name="ck_catalog_supply_nonnegative"),
        sa.CheckConstraint("demand IS NULL OR demand >= 0", name="ck_catalog_demand_nonnegative"),
        sa.CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="ck_catalog_confidence_range"),
        sa.UniqueConstraint("source", "external_id", name="uq_commodity_price_source_external"),
    )
    _create_index_if_missing("ix_commodity_prices_commodity_id", "commodity_prices", ["commodity_id"])
    _create_index_if_missing("ix_commodity_prices_location_id", "commodity_prices", ["location_id"])
    _create_index_if_missing("ix_commodity_prices_observed_at", "commodity_prices", ["observed_at"])
    _create_index_if_missing("ix_commodity_prices_patch_version", "commodity_prices", ["patch_version"])
    _create_index_if_missing("ix_commodity_prices_server_shard", "commodity_prices", ["server_shard"])
    _create_index_if_missing("ix_commodity_prices_source", "commodity_prices", ["source"])
    _create_index_if_missing(
        "ix_commodity_price_latest",
        "commodity_prices",
        ["commodity_id", "location_id", "observed_at"],
    )

    _create_table_if_missing(
        "ships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manufacturer", sa.String(120), nullable=False, server_default=""),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("cargo_capacity_scu", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quantum_fuel", sa.Float(), nullable=True),
        sa.Column("hydrogen_fuel", sa.Float(), nullable=True),
        sa.Column("crew_min", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("crew_max", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price_auec", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("is_flyable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("specifications", sa.JSON(), nullable=False),
        sa.Column("patch_version", sa.String(80), nullable=False, server_default="live"),
        sa.Column("source", sa.String(60), nullable=False, server_default="manual"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cargo_capacity_scu >= 0", name="ck_ship_capacity_nonnegative"),
        sa.CheckConstraint("crew_min >= 0 AND crew_max >= crew_min", name="ck_ship_crew_range"),
        sa.UniqueConstraint("manufacturer", "name", name="uq_ship_manufacturer_name"),
    )
    _create_index_if_missing("ix_ships_name", "ships", ["name"])
    _create_index_if_missing("ix_ships_role", "ships", ["role"])
    _create_index_if_missing("ix_ships_patch_version", "ships", ["patch_version"])
    _create_index_if_missing("ix_ships_is_active", "ships", ["is_active"])

    _create_table_if_missing(
        "components",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grade", sa.String(20), nullable=True),
        sa.Column("manufacturer", sa.String(120), nullable=False, server_default=""),
        sa.Column("source", sa.String(60), nullable=False, server_default="manual"),
        sa.CheckConstraint("size >= 0", name="ck_component_size_nonnegative"),
        sa.UniqueConstraint("name", "type", "size", "manufacturer", name="uq_component_identity"),
    )
    _create_index_if_missing("ix_components_name", "components", ["name"])
    _create_index_if_missing("ix_components_type", "components", ["type"])

    _create_table_if_missing(
        "ship_components",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ship_id", sa.String(36), sa.ForeignKey("ships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_id", sa.String(36), sa.ForeignKey("components.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("ship_id", "component_id", name="uq_ship_component"),
    )
    _create_index_if_missing("ix_ship_components_ship_id", "ship_components", ["ship_id"])
    _create_index_if_missing("ix_ship_components_component_id", "ship_components", ["component_id"])

    _create_table_if_missing(
        "fleet_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fleet_name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_fleet_profiles_fleet_name", "fleet_profiles", ["fleet_name"])

    _create_table_if_missing(
        "fleet_ships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fleet_id", sa.String(36), sa.ForeignKey("fleet_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ship_id", sa.String(36), sa.ForeignKey("ships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("quantity > 0", name="ck_fleet_ship_quantity_positive"),
        sa.UniqueConstraint("fleet_id", "ship_id", name="uq_fleet_ship"),
    )
    _create_index_if_missing("ix_fleet_ships_fleet_id", "fleet_ships", ["fleet_id"])
    _create_index_if_missing("ix_fleet_ships_ship_id", "fleet_ships", ["ship_id"])


def downgrade():
    _drop_table_if_present("fleet_ships")
    _drop_table_if_present("fleet_profiles")
    _drop_table_if_present("ship_components")
    _drop_table_if_present("components")
    _drop_table_if_present("ships")
    _drop_table_if_present("commodity_prices")
    _drop_column_if_present("source_ingestions", "result_json")
    _drop_column_if_present("locations", "moon")
    _drop_column_if_present("locations", "planet")
    _drop_column_if_present("commodities", "updated_at")
    _drop_column_if_present("commodities", "created_at")
    _drop_column_if_present("commodities", "base_price")
    _drop_column_if_present("commodities", "legal_status")
    _drop_column_if_present("commodities", "description")
    _drop_index_if_present("commodities", "ix_commodities_category")
    _drop_column_if_present("commodities", "category")
