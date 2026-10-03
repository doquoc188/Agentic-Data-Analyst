"""Validate the evaluation dataset against the local read-only PostgreSQL fixture."""

import json
import re
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.database import get_connection
from app.tools import _query_validation_error

CASE_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.json"
CATEGORIES = {"basic", "aggregation", "ranking", "temporal", "join", "multi_join", "advanced"}
DIFFICULTIES = {"easy", "medium", "hard"}
REQUIRED_FIELDS = {"id", "category", "difficulty", "question", "reference_sql", "expected_result", "requires"}


class EvaluationCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))

    def test_comparison_contracts_are_explicit_and_consistent(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                contract = case["comparison"]
                self.assertEqual(contract["result_type"], "table" if isinstance(case["expected_result"], dict) else "scalar")
                self.assertIn(contract["column_matching"], {"strict", "position"})
                self.assertIn(contract["row_order"], {"strict", "unordered"})
                self.assertIn(contract["time_granularity"], {None, "month"})
                self.assertIn(contract.get("row_match", "exact"), {"exact", "prefix"})
                if contract.get("row_match") == "prefix":
                    self.assertIn("top_k", case["requires"])
                    self.assertEqual(contract["row_order"], "strict")
                    self.assertTrue(case["expected_result"]["rows"])
                self.assertEqual(contract["numeric_tolerance"], case.get("numeric_tolerance", 0))
                if "top_k" in case["requires"]:
                    self.assertEqual(contract["row_order"], "strict")
                    self.assertIn("tie", case["question"].lower())
                if "required_columns" in contract:
                    self.assertTrue(set(contract["required_columns"]) <= set(case["expected_result"]["columns"]))

    def test_case_format_and_unique_questions(self):
        self.assertEqual(len(self.cases), 24)
        ids = set()
        questions = set()

        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertTrue(REQUIRED_FIELDS <= case.keys())
                self.assertRegex(case["id"], r"^[a-z][a-z0-9_]*$")
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                self.assertIn(case["category"], CATEGORIES)
                self.assertIn(case["difficulty"], DIFFICULTIES)

                question = " ".join(case["question"].casefold().split())
                self.assertTrue(question)
                self.assertNotIn(question, questions)
                questions.add(question)

                query = case["reference_sql"]
                self.assertTrue(query.strip())
                self.assertIsNone(_query_validation_error(query))
                self.assertTrue(case["requires"])
                self.assertEqual(len(case["requires"]), len(set(case["requires"])))
                self.assertIsNotNone(case["expected_result"])

                expected = case["expected_result"]
                if isinstance(expected, dict):
                    self.assertEqual(set(expected), {"columns", "rows"})
                    self.assertTrue(expected["columns"])
                    for row in expected["rows"]:
                        self.assertEqual(len(row), len(expected["columns"]))
                    if len(expected["rows"]) > 1:
                        self.assertIn("ORDER BY", query.upper())
                else:
                    self.assertIsInstance(expected, (int, float, str))

    def test_required_capability_coverage(self):
        count = lambda tag: sum(tag in case["requires"] for case in self.cases)
        self.assertGreaterEqual(count("join"), 8)
        self.assertGreaterEqual(count("join_3") + count("join_4"), 4)
        self.assertGreaterEqual(count("revenue"), 4)
        self.assertGreaterEqual(count("temporal"), 3)
        self.assertGreaterEqual(count("distinct"), 2)
        self.assertGreaterEqual(count("zero_result"), 1)
        self.assertTrue(any(
            "join" in case["requires"]
            and "join_3" not in case["requires"]
            and "join_4" not in case["requires"]
            for case in self.cases
        ))
        for tag in (
            "count", "filter", "group_by", "sum", "average", "sorting",
            "top_k", "multiple_conditions", "customer", "product",
            "category", "city", "having", "multi_step",
        ):
            self.assertGreaterEqual(count(tag), 1, tag)

        for case in self.cases:
            with self.subTest(case=case["id"]):
                query = case["reference_sql"].lower()
                tags = case["requires"]
                if "join" in tags:
                    self.assertIn(" join ", query)
                if "join_3" in tags or "join_4" in tags:
                    tables = set(re.findall(r"public\.(customers|products|orders|order_items)", query))
                    self.assertGreaterEqual(len(tables), 4 if "join_4" in tags else 3)
                if "revenue" in tags:
                    compact_query = "".join(query.split())
                    self.assertIn("oi.quantity*oi.unit_price*(1-oi.discount_pct)", compact_query)
                if "temporal" in tags:
                    self.assertIn("order_date", query)
                if "distinct" in tags:
                    self.assertIn("distinct", query)
                if "multiple_conditions" in tags:
                    self.assertIn(" and ", query)
                if "having" in tags:
                    self.assertIn(" having ", query)
                if "zero_result" in tags:
                    self.assertEqual(case["expected_result"]["rows"], [])

    def test_reference_results_match_postgresql(self):
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SELECT current_user")
                self.assertEqual(cursor.fetchone()[0], "analyst_agent")

                for case in self.cases:
                    with self.subTest(case=case["id"]):
                        cursor.execute(case["reference_sql"])
                        columns = [column.name for column in cursor.description]
                        rows = cursor.fetchall()
                        expected = case["expected_result"]

                        if isinstance(expected, dict):
                            self.assertEqual(columns, expected["columns"])
                            self.assertEqual(len(rows), len(expected["rows"]))
                            for actual_row, expected_row in zip(rows, expected["rows"]):
                                for actual, value in zip(actual_row, expected_row):
                                    self.assert_value_matches(actual, value, case)
                        else:
                            self.assertEqual(len(columns), 1)
                            self.assertEqual(len(rows), 1)
                            self.assert_value_matches(rows[0][0], expected, case)

    def assert_value_matches(self, actual, expected, case):
        if isinstance(actual, Decimal):
            tolerance = Decimal(str(case.get("numeric_tolerance", 0)))
            self.assertLessEqual(abs(actual - Decimal(str(expected))), tolerance)
        elif isinstance(actual, (date, datetime)):
            self.assertEqual(actual.isoformat(), expected)
        else:
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
