"""Import the local Olist CSV files into a fresh agentic_analyst_olist schema."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from psycopg import sql

from app.database import get_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "olist_raw"
DATABASE_NAME = "agentic_analyst_olist"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_text(value: str) -> str:
    return value


def parse_integer(value: str) -> int:
    number = Decimal(value)
    if number != number.to_integral_value():
        raise ValueError("Expected an integer-valued number.")
    return int(number)


def parse_decimal(value: str) -> Decimal:
    return Decimal(value)


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT)


@dataclass(frozen=True)
class Column:
    source: str
    target: str
    parser: Callable[[str], object] = parse_text
    nullable: bool = False


@dataclass(frozen=True)
class TableImport:
    table: str
    filename: str
    expected_rows: int
    columns: tuple[Column, ...]


EXPECTED_SOURCE_FILES = (
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
)

TABLE_IMPORTS = (
    TableImport("customers", "olist_customers_dataset.csv", 99_441, (
        Column("customer_id", "customer_id"),
        Column("customer_unique_id", "customer_unique_id"),
        Column("customer_zip_code_prefix", "zip_code_prefix", parse_integer),
        Column("customer_city", "city"),
        Column("customer_state", "state"),
    )),
    TableImport("products", "olist_products_dataset.csv", 32_951, (
        Column("product_id", "product_id"),
        Column("product_category_name", "category_name", nullable=True),
        Column("product_name_lenght", "name_length", parse_integer, True),
        Column("product_description_lenght", "description_length", parse_integer, True),
        Column("product_photos_qty", "photos_qty", parse_integer, True),
        Column("product_weight_g", "weight_g", parse_integer, True),
        Column("product_length_cm", "length_cm", parse_integer, True),
        Column("product_height_cm", "height_cm", parse_integer, True),
        Column("product_width_cm", "width_cm", parse_integer, True),
    )),
    TableImport("sellers", "olist_sellers_dataset.csv", 3_095, (
        Column("seller_id", "seller_id"),
        Column("seller_zip_code_prefix", "zip_code_prefix", parse_integer),
        Column("seller_city", "city"),
        Column("seller_state", "state"),
    )),
    TableImport("orders", "olist_orders_dataset.csv", 99_441, (
        Column("order_id", "order_id"),
        Column("customer_id", "customer_id"),
        Column("order_status", "status"),
        Column("order_purchase_timestamp", "purchase_timestamp", parse_timestamp),
        Column("order_approved_at", "approved_at", parse_timestamp, True),
        Column("order_delivered_carrier_date", "delivered_carrier_date", parse_timestamp, True),
        Column("order_delivered_customer_date", "delivered_customer_date", parse_timestamp, True),
        Column("order_estimated_delivery_date", "estimated_delivery_date", parse_timestamp),
    )),
    TableImport("order_items", "olist_order_items_dataset.csv", 112_650, (
        Column("order_id", "order_id"),
        Column("order_item_id", "order_item_id", parse_integer),
        Column("product_id", "product_id"),
        Column("seller_id", "seller_id"),
        Column("shipping_limit_date", "shipping_limit_date", parse_timestamp),
        Column("price", "price", parse_decimal),
        Column("freight_value", "freight_value", parse_decimal),
    )),
    TableImport("payments", "olist_order_payments_dataset.csv", 103_886, (
        Column("order_id", "order_id"),
        Column("payment_sequential", "payment_sequential", parse_integer),
        Column("payment_type", "payment_type"),
        Column("payment_installments", "payment_installments", parse_integer),
        Column("payment_value", "payment_value", parse_decimal),
    )),
    TableImport("reviews", "olist_order_reviews_dataset.csv", 99_224, (
        Column("review_id", "review_id"),
        Column("order_id", "order_id"),
        Column("review_score", "review_score", parse_integer),
        Column("review_comment_title", "review_comment_title", nullable=True),
        Column("review_comment_message", "review_comment_message", nullable=True),
        Column("review_creation_date", "review_creation_date", parse_timestamp),
        Column("review_answer_timestamp", "review_answer_timestamp", parse_timestamp),
    )),
    TableImport("category_translation", "product_category_name_translation.csv", 71, (
        Column("product_category_name", "category_name"),
        Column("product_category_name_english", "category_name_english"),
    )),
)


def validate_source_files(raw_dir: Path) -> dict[str, Path]:
    """Return all expected paths or fail before any database connection."""
    if not raw_dir.is_dir():
        raise ValueError(f"Olist raw-data directory does not exist: {raw_dir}")
    paths = {name: raw_dir / name for name in EXPECTED_SOURCE_FILES}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError("Missing expected Olist CSV files: " + ", ".join(missing))
    return paths


def transform_row(spec: TableImport, row: dict[str, str], row_number: int) -> tuple:
    """Rename and parse one CSV row without changing or filling source values."""
    values = []
    for column in spec.columns:
        raw_value = row[column.source]
        if raw_value == "":
            if column.nullable:
                values.append(None)
                continue
            raise ValueError(
                f"{spec.filename} row {row_number}: required field {column.source} is empty."
            )
        try:
            values.append(column.parser(raw_value))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(
                f"{spec.filename} row {row_number}: invalid value in {column.source}."
            ) from exc
    return tuple(values)


def copy_table(cursor, spec: TableImport, path: Path) -> int:
    """Stream one CSV into its fixed table and return the exact row count."""
    expected_header = [column.source for column in spec.columns]
    target_columns = [column.target for column in spec.columns]
    statement = sql.SQL("COPY public.{} ({}) FROM STDIN").format(
        sql.Identifier(spec.table),
        sql.SQL(", ").join(map(sql.Identifier, target_columns)),
    )
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != expected_header:
            raise ValueError(
                f"Unexpected columns in {spec.filename}. "
                f"Expected {expected_header}; found {reader.fieldnames}."
            )
        with cursor.copy(statement) as copy:
            count = 0
            for row_number, row in enumerate(reader, start=2):
                copy.write_row(transform_row(spec, row, row_number))
                count += 1
    if count != spec.expected_rows:
        raise ValueError(
            f"Unexpected row count for {spec.filename}: expected "
            f"{spec.expected_rows}, found {count}."
        )
    return count


def verify_empty_target(cursor) -> None:
    """Require the exact migrated schema and no pre-existing imported rows."""
    cursor.execute("SELECT current_database()")
    if cursor.fetchone()[0] != DATABASE_NAME:
        raise ValueError(f"Import requires the local database {DATABASE_NAME}.")
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    actual_tables = {row[0] for row in cursor.fetchall()}
    expected_tables = {spec.table for spec in TABLE_IMPORTS}
    if actual_tables != expected_tables:
        raise ValueError("Run sql/olist/01_create_schema.sql in a fresh Olist database first.")
    for table in TABLE_IMPORTS:
        cursor.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM public.{} LIMIT 1)").format(
                sql.Identifier(table.table)
            )
        )
        if cursor.fetchone()[0]:
            raise ValueError("Olist import requires all target tables to be empty.")


def import_olist(raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, int]:
    """Validate, import in FK-safe order, and commit all tables atomically."""
    source_paths = validate_source_files(raw_dir)
    counts = {}
    with get_connection(database_name=DATABASE_NAME) as connection:
        with connection.cursor() as cursor:
            verify_empty_target(cursor)
            for spec in TABLE_IMPORTS:
                counts[spec.table] = copy_table(cursor, spec, source_paths[spec.filename])
            for spec in TABLE_IMPORTS:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(spec.table))
                )
                if cursor.fetchone()[0] != spec.expected_rows:
                    raise ValueError(f"Post-import row count failed for {spec.table}.")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-dir", type=Path, default=DEFAULT_RAW_DIR,
        help="Directory containing the nine unmodified Olist CSV files",
    )
    args = parser.parse_args()
    try:
        counts = import_olist(args.raw_dir.resolve())
    except ValueError as exc:
        parser.exit(1, f"Olist import stopped: {exc}\n")
    except Exception as exc:
        parser.exit(1, f"Olist import stopped ({type(exc).__name__}); no credentials logged.\n")
    for table, count in counts.items():
        print(f"{table}: {count}")
    print("Olist import committed successfully.")


if __name__ == "__main__":
    main()
