"""Deterministic checks for the frozen Olist benchmark."""

import copy
import hashlib
import json
import os
import re
import unittest
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from app.agent import ToolCallRecord
from app.profiles import DatabaseTarget
from app.tools import _query_validation_error
from eval.olist.verify import (
    CASE_PATH,
    DATABASE_NAME,
    derive_expected_results,
    execute_verification_sql,
    require_database,
    verify_stored_results,
)
from eval.olist_runner import run_olist
from eval.runner import compare_result, load_cases

ROOT = Path(__file__).resolve().parent.parent
MANUAL_DEVELOPMENT_QUESTIONS = (
    "How many orders are in the dataset?",
    "Which order status occurs most often?",
    "Which payment type is used most often?",
    "Which product category generated the most item revenue?",
    "Which seller generated the highest item revenue?",
    "How many unique customers placed more than one order?",
)
FIXTURE_HASHES = {
    ROOT / "eval" / "cases.json":
        "6c0a016fae259a51b79d63c669d90ce4197dbbbf25e49664d2ed16b869fa369f",
    ROOT / "eval" / "generalization" / "cases.json":
        "5c25eb1812677ebdfb9812c0c56eadfcbe1fc54b140aa80176fbf8f549302bd7",
}
OLIST_CASE_IDS = (
    "olist_average_review_score",
    "olist_orders_missing_delivery_timestamp",
    "olist_review_comment_percentage",
    "olist_max_payment_installments",
    "olist_orders_by_purchase_month",
    "olist_late_delivery_percentage",
    "olist_top_states_by_unique_customers",
    "olist_multi_payment_order_count",
    "olist_top_categories_by_average_freight",
    "olist_review_score_by_order_status",
    "olist_top_seller_states_by_delivered_orders",
    "olist_credit_card_multi_installment_percentage",
    "olist_state_late_delivery_rates",
    "olist_top_categories_by_review_score",
    "olist_top_sellers_by_category_diversity",
    "olist_customers_across_multiple_states",
    "olist_month_highest_average_delivery_variance",
    "olist_delivered_payment_item_total_mismatches",
    "olist_top_categories_by_freight_ratio",
    "olist_largest_monthly_delivered_order_increase",
)
OLIST_FROZEN_FIELD_HASHES = {
    "ids": "ac53db11ae60f029eb8cda9c13d054da8feba49022367f5a7496435481364f74",
    "sql": "0d731319dcb234c1ab18d90573e1117a0cb895dd8b4aca4585934d3bd82c2f05",
    "expected": "7f343e2c09afbf8a74c13ec5168970d3328fc1dc75b87caf0a21c7eda3ed8d89",
}


def normalized_question(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.casefold()))


def selected_field_hash(cases: list[dict], fields: set[str]) -> str:
    selected = [{key: value for key, value in case.items() if key in fields}
                for case in cases]
    payload = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class OlistBenchmarkCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(CASE_PATH)

    def test_exact_count_unique_ids_questions_and_difficulty_distribution(self):
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(tuple(case["id"] for case in self.cases), OLIST_CASE_IDS)
        self.assertEqual(len({case["id"] for case in self.cases}), 20)
        self.assertEqual(len({case["question"] for case in self.cases}), 20)
        self.assertEqual(
            Counter(case["difficulty"] for case in self.cases),
            {"easy": 4, "medium": 8, "hard": 8},
        )

    def test_frozen_ids_reference_sql_and_expected_results_are_unchanged(self):
        self.assertEqual(
            selected_field_hash(self.cases, {"id"}),
            OLIST_FROZEN_FIELD_HASHES["ids"],
        )
        self.assertEqual(
            selected_field_hash(self.cases, {"id", "reference_sql"}),
            OLIST_FROZEN_FIELD_HASHES["sql"],
        )
        self.assertEqual(
            selected_field_hash(
                self.cases,
                {"id", "expected_columns", "expected_result"},
            ),
            OLIST_FROZEN_FIELD_HASHES["expected"],
        )

    def test_questions_have_no_unexplained_eligibility_shorthand(self):
        unexplained = re.compile(
            r"\b(?:qualifying|eligible|significant|active|large)\b|\benough data\b",
            re.IGNORECASE,
        )
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIsNone(unexplained.search(case["question"]))

    def test_required_metadata_read_only_sql_and_expected_results(self):
        required = {
            "id", "question", "difficulty", "category", "requires",
            "reference_sql", "expected_columns", "expected_result",
            "ground_truth_verified", "comparison",
        }
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(required <= case.keys())
                self.assertRegex(case["id"], r"^olist_[a-z0-9_]+$")
                self.assertTrue(case["category"] and case["requires"])
                self.assertEqual(len(case["requires"]), len(set(case["requires"])))
                self.assertIsNone(_query_validation_error(case["reference_sql"]))
                self.assertNotIn("SELECT *", case["reference_sql"].upper())
                self.assertTrue(case["expected_columns"])
                self.assertTrue(case["ground_truth_verified"])
                self.assertIsNotNone(case["expected_result"])

                contract = case["comparison"]
                self.assertIn(contract["result_type"], {"scalar", "table"})
                self.assertIn(contract["column_matching"], {"strict", "position"})
                self.assertIn(contract["row_order"], {"strict", "unordered"})
                actual = case["expected_result"]
                if contract["result_type"] == "scalar":
                    self.assertIsInstance(actual, (int, float, str))
                    actual = {"columns": case["expected_columns"], "rows": [[actual]]}
                else:
                    self.assertEqual(set(actual), {"columns", "rows"})
                    self.assertEqual(actual["columns"], case["expected_columns"])
                    self.assertTrue(
                        all(len(row) == len(actual["columns"]) for row in actual["rows"])
                    )
                    self.assertEqual(contract["required_columns"], case["expected_columns"])
                    if len(actual["rows"]) > 1:
                        self.assertIn("ORDER BY", case["reference_sql"].upper())
                self.assertTrue(
                    compare_result(
                        actual,
                        case["expected_result"],
                        comparison=contract,
                    )[0]
                )

    def test_ranking_contracts_state_ties_and_use_stable_sql_ordering(self):
        for case in self.cases:
            if "top_k" not in case["requires"]:
                continue
            with self.subTest(case=case["id"]):
                self.assertIn("tie", case["question"].casefold())
                self.assertIn("ORDER BY", case["reference_sql"].upper())
                self.assertIn("LIMIT", case["reference_sql"].upper())
                self.assertEqual(case["comparison"]["row_order"], "strict")
                self.assertEqual(case["comparison"]["row_match"], "prefix")

    def test_manual_development_questions_and_trivial_paraphrases_are_excluded(self):
        manual = {normalized_question(question) for question in MANUAL_DEVELOPMENT_QUESTIONS}
        for case in self.cases:
            question = normalized_question(case["question"])
            with self.subTest(case=case["id"]):
                self.assertNotIn(question, manual)
                similarity = max(
                    SequenceMatcher(None, question, excluded).ratio()
                    for excluded in manual
                )
                self.assertLess(similarity, 0.70)

        # Explicitly protect the six excluded business intents, not just wording.
        questions = "\n".join(case["question"].casefold() for case in self.cases)
        self.assertNotRegex(
            questions,
            r"(^|\n)(how many orders (are there|exist|are in)|"
            r"what is the (total )?(number|count) of orders|what is the total order count)",
        )
        self.assertNotRegex(questions, r"order status .*(most common|most often)")
        self.assertNotRegex(questions, r"payment type .*(most common|most often)")
        self.assertNotRegex(questions, r"product category .*most item revenue")
        self.assertNotRegex(questions, r"seller .*highest item revenue")
        self.assertNotRegex(questions, r"unique customers .*more than one order")

    def test_business_skill_coverage(self):
        categories = Counter(case["category"] for case in self.cases)
        self.assertEqual(
            set(categories),
            {"reviews", "delivery", "payments", "temporal", "customers", "products", "sellers"},
        )
        tags = Counter(tag for case in self.cases for tag in case["requires"])
        for tag in (
            "aggregation", "join", "join_3", "join_4", "join_5", "temporal",
            "distinct", "percentage", "average", "freight", "payment_rows",
            "review_rows", "semantic_comments", "customer_identity", "window",
            "multi_step", "top_k",
        ):
            self.assertGreater(tags[tag], 0, tag)
        self.assertGreaterEqual(tags["join"], 8)
        self.assertGreaterEqual(tags["temporal"], 5)

    def test_existing_sales_and_saas_fixtures_are_byte_for_byte_unchanged(self):
        for path, expected_hash in FIXTURE_HASHES.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)

    def test_existing_evaluator_can_run_an_olist_case_with_offline_fakes(self):
        case = copy.deepcopy(self.cases[0])
        case.update(expected_result=7, reference_sql="SELECT evaluator_only_reference")

        def fake_agent(question, *, trace, verbose):
            self.assertEqual(question, case["question"])
            self.assertFalse(verbose)
            self.assertEqual(trace.source, "eval_olist")
            trace.model_turns = 1
            trace.tool_calls.append(
                ToolCallRecord(
                    "execute_sql",
                    {"query": "SELECT 7 AS answer"},
                    "COLUMNS:\nanswer\nROWS:\n7\n(1 row)",
                    "offline-sql",
                )
            )
            return "The answer is 7."

        def fake_database(query):
            if query == "SELECT current_database() AS database_name":
                return {"columns": ["database_name"], "rows": [[DATABASE_NAME]]}
            self.assertEqual(query, "SELECT 7 AS answer")
            return {"columns": ["answer"], "rows": [[7]]}

        with TemporaryDirectory() as directory, patch(
            "app.trace.RUNS_DIR", Path(directory)
        ), patch(
            "eval.olist_runner.require_local_olist_target",
            return_value=DatabaseTarget(database_name=DATABASE_NAME),
        ):
            report = run_olist([case], fake_agent, fake_database)

        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["total_cases"], 1)


class OlistBenchmarkVerificationTests(unittest.TestCase):
    def mock_connection(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("analyst_agent", DATABASE_NAME)
        cursor.description = [Mock(name="column")]
        cursor.description[0].name = "value"
        cursor.fetchmany.return_value = [(7,)]
        return connection, cursor

    def test_verifier_uses_read_only_timeout_row_cap_and_rollback(self):
        connection, cursor = self.mock_connection()
        target = DatabaseTarget(database_name=DATABASE_NAME)
        with patch(
            "eval.olist.verify.require_local_olist_target", return_value=target
        ), patch("eval.olist.verify.get_connection", return_value=connection):
            result = execute_verification_sql("SELECT 7 AS value")

        self.assertEqual(result, {"columns": ["value"], "rows": [[7]]})
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[0], "SET TRANSACTION READ ONLY")
        self.assertIn("statement_timeout", statements[1])
        self.assertEqual(statements[-1], "SELECT 7 AS value")
        cursor.fetchmany.assert_called_once_with(101)
        connection.rollback.assert_called_once()

    def test_write_sql_is_rejected_before_target_or_connection_resolution(self):
        for query in (
            "DELETE FROM public.orders",
            "INSERT INTO public.orders DEFAULT VALUES",
            "DROP TABLE public.orders",
            "SET TRANSACTION READ ONLY",
        ):
            with self.subTest(query=query), patch(
                "eval.olist.verify.require_local_olist_target"
            ) as target, patch("eval.olist.verify.get_connection") as connect:
                with self.assertRaises(ValueError):
                    execute_verification_sql(query)
                target.assert_not_called()
                connect.assert_not_called()

    def test_ground_truth_derivation_uses_query_results(self):
        cases = copy.deepcopy(load_cases(CASE_PATH))
        by_query = {case["reference_sql"]: case for case in cases}

        def fake_database(query):
            case = by_query[query]
            columns = case["expected_columns"]
            if case["comparison"]["result_type"] == "scalar":
                rows = [[7]]
            else:
                rows = [["fixture-only"] * len(columns)]
            return {"columns": columns, "rows": rows}

        derived = derive_expected_results(cases, fake_database)
        self.assertEqual(len(derived), 20)
        self.assertTrue(all(case["ground_truth_verified"] for case in derived))
        self.assertEqual(derived[0]["expected_result"], 7)
        self.assertIsNot(derived[0], cases[0])

    def test_stored_result_verifier_checks_database_and_all_cases(self):
        cases = load_cases(CASE_PATH)
        by_query = {case["reference_sql"]: case for case in cases}

        def fake_database(query):
            if query == "SELECT current_database() AS database_name":
                return {"columns": ["database_name"], "rows": [[DATABASE_NAME]]}
            case = by_query[query]
            expected = case["expected_result"]
            if case["comparison"]["result_type"] == "scalar":
                return {"columns": case["expected_columns"], "rows": [[expected]]}
            return copy.deepcopy(expected)

        verified = verify_stored_results(fake_database)
        self.assertEqual(len(verified), 20)

    def test_wrong_database_stops_reference_verification(self):
        database = Mock(
            return_value={"columns": ["database_name"], "rows": [["agentic_analyst"]]}
        )
        with self.assertRaisesRegex(ValueError, "agentic_analyst_olist"):
            require_database(database)
        database.assert_called_once()


@unittest.skipUnless(
    os.getenv("RUN_OLIST_BENCHMARK_DB_TESTS") == "1",
    "Enable explicitly for local read-only Olist reference verification.",
)
class OlistBenchmarkDatabaseTests(unittest.TestCase):
    def test_frozen_reference_results_match_local_postgresql(self):
        verified = verify_stored_results()
        self.assertEqual(len(verified), 20)
        self.assertTrue(all(case["ground_truth_verified"] for case in verified))


if __name__ == "__main__":
    unittest.main()
