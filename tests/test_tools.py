"""Tests for the standalone calculator tool."""

import unittest

from app.tools import calculator


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


if __name__ == "__main__":
    unittest.main()
