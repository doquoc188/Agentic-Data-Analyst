"""Offline checks for the deterministic local Olist importer."""

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sql.olist.import_olist import (
    EXPECTED_SOURCE_FILES, TABLE_IMPORTS, import_olist, validate_source_files,
    transform_row,
)


SPECS = {spec.table: spec for spec in TABLE_IMPORTS}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

PRICE_COMMENT = (
    "Item price excluding freight_value. The source metadata does not explicitly "
    "declare a currency unit; consumers must not infer or display a currency symbol."
)
FREIGHT_COMMENT = (
    "Freight or shipping amount for this item. The source metadata does not explicitly "
    "declare a currency unit; consumers must not infer or display a currency symbol."
)
PAYMENT_COMMENT = (
    "Amount for one payment row. An order can have multiple payment rows; sum "
    "payment_value by order_id for total payment amount. The source metadata does not "
    "explicitly declare a currency unit; consumers must not infer or display a currency symbol."
)


class OlistImportTests(unittest.TestCase):
    def test_missing_source_file_fails_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            for name in EXPECTED_SOURCE_FILES[:-1]:
                (raw / name).touch()
            with patch("sql.olist.import_olist.get_connection") as connect:
                with self.assertRaisesRegex(ValueError, "product_category_name_translation.csv"):
                    import_olist(raw)
            connect.assert_not_called()

    def test_all_expected_files_validate_including_deferred_geolocation(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            for name in EXPECTED_SOURCE_FILES:
                (raw / name).touch()
            paths = validate_source_files(raw)
        self.assertEqual(tuple(paths), EXPECTED_SOURCE_FILES)
        self.assertIn("olist_geolocation_dataset.csv", paths)

    def test_table_order_is_foreign_key_safe_and_excludes_geolocation(self):
        self.assertEqual(
            tuple(spec.table for spec in TABLE_IMPORTS),
            ("customers", "products", "sellers", "orders", "order_items",
             "payments", "reviews", "category_translation"),
        )
        self.assertNotIn("geolocation", SPECS)
        self.assertTrue(all("geolocation" not in spec.filename for spec in TABLE_IMPORTS))

    def test_column_renaming_matches_target_schema(self):
        self.assertEqual(
            {column.source: column.target for column in SPECS["products"].columns},
            {
                "product_id": "product_id",
                "product_category_name": "category_name",
                "product_name_lenght": "name_length",
                "product_description_lenght": "description_length",
                "product_photos_qty": "photos_qty",
                "product_weight_g": "weight_g",
                "product_length_cm": "length_cm",
                "product_height_cm": "height_cm",
                "product_width_cm": "width_cm",
            },
        )
        self.assertEqual(
            {column.source: column.target for column in SPECS["customers"].columns},
            {
                "customer_id": "customer_id", "customer_unique_id": "customer_unique_id",
                "customer_zip_code_prefix": "zip_code_prefix", "customer_city": "city",
                "customer_state": "state",
            },
        )

    def test_import_mapping_parses_timestamps_numbers_and_decimals(self):
        row = {
            "order_id": "order-1", "order_item_id": "2", "product_id": "product-1",
            "seller_id": "seller-1", "shipping_limit_date": "2017-10-10 15:07:15",
            "price": "58.90", "freight_value": "13.29",
        }
        values = transform_row(SPECS["order_items"], row, 2)
        self.assertEqual(values[:4], ("order-1", 2, "product-1", "seller-1"))
        self.assertEqual(values[4], datetime(2017, 10, 10, 15, 7, 15))
        self.assertEqual(values[5:], (Decimal("58.90"), Decimal("13.29")))

    def test_nullable_fields_remain_none_without_filling(self):
        row = {
            "product_id": "product-1", "product_category_name": "",
            "product_name_lenght": "", "product_description_lenght": "",
            "product_photos_qty": "", "product_weight_g": "",
            "product_length_cm": "", "product_height_cm": "", "product_width_cm": "",
        }
        self.assertEqual(
            transform_row(SPECS["products"], row, 2),
            ("product-1", None, None, None, None, None, None, None, None),
        )

    def test_integer_targets_accept_integral_decimal_csv_values_only(self):
        row = {
            "product_id": "product-1", "product_category_name": "moveis_decoracao",
            "product_name_lenght": "58.0", "product_description_lenght": "598.0",
            "product_photos_qty": "4.0", "product_weight_g": "650.0",
            "product_length_cm": "28.0", "product_height_cm": "9.0",
            "product_width_cm": "14.0",
        }
        self.assertEqual(
            transform_row(SPECS["products"], row, 2)[2:],
            (58, 598, 4, 650, 28, 9, 14),
        )
        row["product_weight_g"] = "650.5"
        with self.assertRaisesRegex(ValueError, "product_weight_g"):
            transform_row(SPECS["products"], row, 2)

    def test_required_empty_field_fails_clearly(self):
        row = {
            "customer_id": "", "customer_unique_id": "customer-unique-1",
            "customer_zip_code_prefix": "14409", "customer_city": "franca",
            "customer_state": "SP",
        }
        with self.assertRaisesRegex(ValueError, "required field customer_id"):
            transform_row(SPECS["customers"], row, 2)

    def test_expected_counts_and_exact_source_headers_are_declared(self):
        self.assertEqual(
            {spec.table: spec.expected_rows for spec in TABLE_IMPORTS},
            {
                "customers": 99441, "products": 32951, "sellers": 3095,
                "orders": 99441, "order_items": 112650, "payments": 103886,
                "reviews": 99224, "category_translation": 71,
            },
        )
        self.assertEqual(
            [column.source for column in SPECS["orders"].columns],
            ["order_id", "customer_id", "order_status", "order_purchase_timestamp",
             "order_approved_at", "order_delivered_carrier_date",
             "order_delivered_customer_date", "order_estimated_delivery_date"],
        )

    def test_monetary_comments_are_consistent_and_forbid_currency_inference(self):
        sql_paths = (
            REPOSITORY_ROOT / "sql" / "olist" / "01_create_schema.sql",
            REPOSITORY_ROOT / "sql" / "olist" / "04_monetary_metadata.sql",
            REPOSITORY_ROOT / "sql" / "olist" / "02_verify.sql",
        )
        for path in sql_paths:
            sql = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn(PRICE_COMMENT, sql)
                self.assertIn(FREIGHT_COMMENT, sql)
                self.assertIn(PAYMENT_COMMENT, sql)


if __name__ == "__main__":
    unittest.main()
