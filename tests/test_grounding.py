"""Deterministic tests for conservative final-answer evidence cleanup."""

import unittest

from app.grounding import guard_final_answer


def rows(*values: str) -> str:
    return "COLUMNS:\ncategory | total\n\nROWS:\n" + " | ".join(values) + "\n\nRows returned: 1"


class FinalAnswerGroundingTests(unittest.TestCase):
    def test_removes_unsupported_currency_symbol_without_changing_number(self):
        answer = guard_final_answer(
            "Which category has the highest total?",
            [rows("category_alpha", "1258681.34")],
            "category_alpha has a total of $1,258,681.34.",
        )
        self.assertEqual(answer, "category_alpha has a total of 1,258,681.34.")

    def test_preserves_currency_symbol_supported_by_input_or_evidence(self):
        cases = (
            ("Report the total in $.", rows("category_alpha", "1250.00")),
            ("Report the total.", rows("category_alpha", "$1250.00")),
        )
        for question, evidence in cases:
            with self.subTest(question=question):
                self.assertEqual(
                    guard_final_answer(question, [evidence], "The total is $1,250.00."),
                    "The total is $1,250.00.",
                )

    def test_removes_unsupported_generic_currency_code(self):
        self.assertEqual(
            guard_final_answer(
                "Report the total.",
                [rows("category_alpha", "1250.00")],
                "The total is XYZ 1,250.00.",
            ),
            "The total is 1,250.00.",
        )

    def test_removes_unsupported_parenthetical_alias(self):
        answer = guard_final_answer(
            "Which category leads?",
            [rows("category_alpha", "1250.00")],
            "The leader is **category_alpha** (Category Alpha).",
        )
        self.assertEqual(answer, "The leader is **category_alpha**.")

    def test_preserves_parenthetical_label_supported_by_evidence(self):
        answer = guard_final_answer(
            "Which category leads?",
            [rows("category_alpha", "Category Alpha", "1250.00")],
            "The leader is category_alpha (Category Alpha).",
        )
        self.assertEqual(answer, "The leader is category_alpha (Category Alpha).")

    def test_preserves_ordinary_explanatory_parentheses(self):
        answer = guard_final_answer(
            "How many records matched?",
            [rows("category_alpha", "42")],
            "There are 42 records (after filtering).",
        )
        self.assertEqual(answer, "There are 42 records (after filtering).")

    def test_never_changes_supported_values_or_numbers(self):
        answer = guard_final_answer(
            "Which category leads?",
            [rows("category_alpha", "1258681.34")],
            "category_alpha (Friendly Label) leads with $1,258,681.34.",
        )
        self.assertEqual(answer, "category_alpha leads with 1,258,681.34.")
        self.assertIn("category_alpha", answer)
        self.assertIn("1,258,681.34", answer)


if __name__ == "__main__":
    unittest.main()
