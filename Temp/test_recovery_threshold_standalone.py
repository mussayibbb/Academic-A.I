"""Standalone logic test for the academic recovery threshold.

This is an independent reference test, not an application or end-to-end test.
It deliberately imports no project modules and performs no database access.
"""

from __future__ import annotations

import math
import re
import unittest
from dataclasses import dataclass
from decimal import Decimal


THRESHOLD_SCORE = Decimal("12")
THRESHOLD_MAXIMUM = Decimal("25")
FA1_TITLE = re.compile(r"\bFA[\s_-]*1\b", re.IGNORECASE)


@dataclass(frozen=True)
class Eligibility:
    status: str
    support_triggers: tuple[str, ...]
    normalized_score: Decimal | None
    evidence_status: str


def evaluate_verified_fa1(
    *,
    assessment_title: str | None,
    score: int | float | None,
    maximum_marks: int | float | None,
    maximum_marks_verified: bool,
) -> Eligibility:
    """Apply the documented strict-below-12/25 rule to verified FA-1 evidence."""
    if not assessment_title or not FA1_TITLE.search(assessment_title):
        return Eligibility("INSUFFICIENT_EVIDENCE", (), None, "INSUFFICIENT_EVIDENCE")

    valid_score = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        and score >= 0
    )
    valid_maximum = (
        isinstance(maximum_marks, (int, float))
        and not isinstance(maximum_marks, bool)
        and math.isfinite(maximum_marks)
        and maximum_marks > 0
    )
    if (
        not valid_score
        or not valid_maximum
        or maximum_marks_verified is not True
        or score > maximum_marks
    ):
        return Eligibility("INSUFFICIENT_EVIDENCE", (), None, "INSUFFICIENT_EVIDENCE")

    normalized_score = (
        Decimal(str(score)) * THRESHOLD_MAXIMUM / Decimal(str(maximum_marks))
    )
    if normalized_score < THRESHOLD_SCORE:
        return Eligibility("LOW", ("LOW_SCORE",), normalized_score, "VERIFIED")
    return Eligibility("NOT_LOW", (), normalized_score, "VERIFIED")


class RecoveryThresholdStandaloneTests(unittest.TestCase):
    def evaluate(self, score: int | float) -> Eligibility:
        return evaluate_verified_fa1(
            assessment_title="Synthetic FA-1 test",
            score=score,
            maximum_marks=25,
            maximum_marks_verified=True,
        )

    def test_10_of_25_is_low_with_low_score_trigger(self) -> None:
        result = self.evaluate(10)

        self.assertEqual(result.status, "LOW")
        self.assertIn("LOW_SCORE", result.support_triggers)
        self.assertEqual(result.normalized_score, Decimal("10"))
        self.assertEqual(result.evidence_status, "VERIFIED")

    def test_11_of_25_is_low(self) -> None:
        result = self.evaluate(11)

        self.assertEqual(result.status, "LOW")
        self.assertIn("LOW_SCORE", result.support_triggers)

    def test_exactly_12_of_25_is_not_low_without_low_score_trigger(self) -> None:
        result = self.evaluate(12)

        self.assertEqual(result.status, "NOT_LOW")
        self.assertNotIn("LOW_SCORE", result.support_triggers)
        self.assertEqual(result.normalized_score, Decimal("12"))

    def test_13_of_25_is_not_low(self) -> None:
        result = self.evaluate(13)

        self.assertEqual(result.status, "NOT_LOW")
        self.assertNotIn("LOW_SCORE", result.support_triggers)

    def test_missing_and_unverified_evidence_is_insufficient(self) -> None:
        cases = (
            {"score": None, "maximum_marks": 25, "maximum_marks_verified": True},
            {"score": 10, "maximum_marks": None, "maximum_marks_verified": True},
            {"score": 10, "maximum_marks": 25, "maximum_marks_verified": False},
            {"score": 10, "maximum_marks": 25, "maximum_marks_verified": None},
        )
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result = evaluate_verified_fa1(
                    assessment_title="Synthetic FA-1 test",
                    **evidence,
                )
                self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
                self.assertNotIn("LOW_SCORE", result.support_triggers)

    def test_invalid_score_or_maximum_is_insufficient(self) -> None:
        invalid_cases = (
            {"score": -1, "maximum_marks": 25},
            {"score": 26, "maximum_marks": 25},
            {"score": float("nan"), "maximum_marks": 25},
            {"score": float("inf"), "maximum_marks": 25},
            {"score": "10", "maximum_marks": 25},
            {"score": 10, "maximum_marks": 0},
            {"score": 10, "maximum_marks": -25},
            {"score": 10, "maximum_marks": float("nan")},
            {"score": 10, "maximum_marks": float("inf")},
            {"score": 10, "maximum_marks": "25"},
        )
        for evidence in invalid_cases:
            with self.subTest(evidence=evidence):
                result = evaluate_verified_fa1(
                    assessment_title="Synthetic FA-1 test",
                    maximum_marks_verified=True,
                    **evidence,
                )
                self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
                self.assertNotIn("LOW_SCORE", result.support_triggers)

    def test_non_fa1_or_missing_title_is_insufficient(self) -> None:
        for title in (None, "", "Synthetic FA-2 test"):
            with self.subTest(title=title):
                result = evaluate_verified_fa1(
                    assessment_title=title,
                    score=10,
                    maximum_marks=25,
                    maximum_marks_verified=True,
                )
                self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
