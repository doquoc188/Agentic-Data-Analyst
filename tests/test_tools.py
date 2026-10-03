"""Tests for the standalone tools."""

import unittest
from unittest.mock import MagicMock, patch

from app.tools import MAX_ROWS, calculator, describe_table, execute_sql, get_schema


class CalculatorTests(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(calculator.invoke({"operation": "add", "a": 2, "b": 3}), 5)

    def test_subtraction(self):
        self.assertEqual(calculator.invoke({"operation": "subtract", "a": 7, "b": 4}), 3)

    def test_multiplication(self):
        self.assertEqual(calculator.invoke({"operation": "multiply", "a": 6, "b": 3}), 18)

    def test_division(self):
        self.assertEqual(calculator.invoke({"operation": "divide", "a": 7, "b": 2}), 3.5)

    def test_division_by_zero(self):
        with self.assertRaisesRegex(ZeroDivisionError, "Cannot divide by zero"):
            calculator.invoke({"operation": "divide", "a": 7, "b": 0})


class SchemaTests(unittest.TestCase):
    def test_get_schema_includes_sales(self):
        schema = get_schema.invoke({})
        self.assertIn("TABLE: sales", schema)


class DescribeTableTests(unittest.TestCase):
    def test_sales(self):
        description = describe_table.invoke({"table_name": "sales"})
        self.assertIn("TABLE: sales", description)
        self.assertIn("COLUMNS:", description)
        self.assertIn("- order_id: integer", description)
        self.assertIn("PRIMARY KEY:\n- order_id\n", description)
        self.assertIn("FOREIGN KEYS:", description)

    def test_orders_primary_and_foreign_key(self):
        description = describe_table.invoke({"table_name": "orders"})
        self.assertIn("PRIMARY KEY:\n- order_id\n", description)
        self.assertIn(
            "FOREIGN KEY (customer_id) REFERENCES customers(customer_id)",
            description,
        )

    def test_order_items_primary_and_foreign_keys(self):
        description = describe_table.invoke({"table_name": "order_items"})
        self.assertIn("PRIMARY KEY:\n- order_item_id\n", description)
        self.assertIn(
            "FOREIGN KEY (order_id) REFERENCES orders(order_id)", description
        )
        self.assertIn(
            "FOREIGN KEY (product_id) REFERENCES products(product_id)",
            description,
        )

    def test_customers_and_products_primary_keys(self):
        for table_name, key_column in (
            ("customers", "customer_id"),
            ("products", "product_id"),
        ):
            with self.subTest(table_name=table_name):
                description = describe_table.invoke({"table_name": table_name})
                self.assertIn(f"PRIMARY KEY:\n- {key_column}\n", description)

    def test_composite_primary_key_keeps_database_column_order(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor.fetchall.side_effect = [
            [("first_part", "integer", "NO", None, None),
             ("second_part", "integer", "NO", None, None)],
            [("second_part",), ("first_part",)],
            [],
        ]
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        with patch("app.tools.get_connection", return_value=connection):
            description = describe_table.invoke({"table_name": "example"})

        self.assertIn("PRIMARY KEY:\n- second_part\n- first_part\n", description)

    def test_missing_table(self):
        description = describe_table.invoke({"table_name": "does_not_exist"})
        self.assertEqual(
            description,
            "Table 'does_not_exist' was not found in the public schema.",
        )


class ColumnCommentTests(unittest.TestCase):
    def test_description_comes_from_parameterized_catalog_lookup(self):
        comment = "Fractional discount rate: 0.10 means a 10% discount."
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor.fetchall.side_effect = [
            [("discount_pct", "numeric", "NO", "0", comment),
             ("quantity", "integer", "NO", None, None)],
            [], [],
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        cursor.__enter__.return_value = cursor
        table_name = "table' supplied by caller"
        with patch("app.tools.get_connection", return_value=connection):
            description = describe_table.invoke({"table_name": table_name})
        self.assertIn("- discount_pct: numeric, NOT NULL, DEFAULT 0\n  description: " + comment, description)
        self.assertIn("- quantity: integer, NOT NULL\n\nPRIMARY KEY:", description)
        for call in cursor.execute.call_args_list:
            self.assertEqual(call.args[1], (table_name,))
            self.assertNotIn(table_name, call.args[0])
        self.assertIn("col_description", cursor.execute.call_args_list[1].args[0])
        cursor.__exit__.assert_called_once()
        connection.__exit__.assert_called_once()


class ExecuteSqlTests(unittest.TestCase):
    def test_select_count(self):
        result = execute_sql.invoke({"query": "SELECT COUNT(*) AS total_rows FROM sales;"})
        self.assertIn("total_rows", result)
        self.assertIn("\n300\n", result)

    def test_grouped_query(self):
        result = execute_sql.invoke({
            "query": "SELECT category, COUNT(*) AS count FROM sales "
                     "GROUP BY category ORDER BY count DESC;"
        })
        self.assertIn("category | count", result)
        self.assertIn("Rows returned:", result)

    def test_cte(self):
        result = execute_sql.invoke({
            "query": "WITH completed AS (SELECT * FROM sales WHERE status = 'Completed') "
                     "SELECT COUNT(*) AS completed_count FROM completed;"
        })
        self.assertIn("completed_count", result)
        self.assertIn("Rows returned: 1", result)

    def test_empty_query(self):
        result = execute_sql.invoke({"query": "   "})
        self.assertIn("SQL query is empty", result)

    def test_unsafe_queries_are_rejected_before_connection(self):
        with patch("app.tools.get_connection") as connect:
            for query in (
                "DELETE FROM sales;",
                "DROP TABLE sales;",
                "SELECT 1; DELETE FROM sales;",
                "WITH changed AS (DELETE FROM sales RETURNING *) SELECT * FROM changed;",
            ):
                self.assertIn("Query rejected:", execute_sql.invoke({"query": query}))
            connect.assert_not_called()

        result = execute_sql.invoke({"query": "SELECT COUNT(*) FROM sales;"})
        self.assertIn("\n300\n", result)

    def test_missing_column(self):
        result = execute_sql.invoke({"query": "SELECT does_not_exist FROM sales;"})
        self.assertEqual(result, "SQL execution error: column does not exist.")

    def test_truncated_result(self):
        result = execute_sql.invoke({"query": "SELECT * FROM sales ORDER BY order_id;"})
        row_section = result.split("ROWS:\n", 1)[1].split("\n\nRows returned:", 1)[0]
        self.assertEqual(len(row_section.splitlines()), MAX_ROWS)
        self.assertIn(f"Result truncated to {MAX_ROWS} rows.", result)

    def test_zero_rows(self):
        result = execute_sql.invoke({"query": "SELECT order_id FROM sales WHERE 1 = 0;"})
        self.assertIn("(no rows)", result)
        self.assertIn("Rows returned: 0", result)

    def test_transaction_is_read_only(self):
        result = execute_sql.invoke({
            "query": "SELECT current_setting('transaction_read_only') AS read_only;"
        })
        self.assertIn("read_only", result)
        self.assertIn("\non\n", result)

    def test_invalid_syntax(self):
        result = execute_sql.invoke({"query": "SELECT * FORM sales;"})
        self.assertEqual(result, "SQL execution error: invalid SQL syntax.")


if __name__ == "__main__":
    unittest.main()
