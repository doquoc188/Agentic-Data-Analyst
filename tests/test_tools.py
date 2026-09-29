"""Tests for the standalone tools."""

import unittest

from app.tools import calculator, describe_table, get_schema


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
        self.assertIn("PRIMARY KEY:", description)
        self.assertIn("FOREIGN KEYS:", description)

    def test_missing_table(self):
        description = describe_table.invoke({"table_name": "does_not_exist"})
        self.assertEqual(
            description,
            "Table 'does_not_exist' was not found in the public schema.",
        )


if __name__ == "__main__":
    unittest.main()
