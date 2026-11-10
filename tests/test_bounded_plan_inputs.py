"""Constructor iterators must stop at admission sentinels, before over-reading."""
import unittest
from tabular_drift_audit import ColumnPlan, ComparisonPlan, DriftError


class OverReadError(RuntimeError):
    pass


class TestBoundedPlanInputs(unittest.TestCase):
    def test_cutpoint_iterator_stops_after_limit_plus_one(self):
        def values():
            yield from range(16)
            raise OverReadError("Constructor read beyond the sixteen-value sentinel")
        with self.assertRaises(DriftError) as caught:
            ColumnPlan("x", "numeric", values())
        self.assertEqual(caught.exception.status, "UNKNOWN_BUDGET")
        self.assertEqual(caught.exception.reason_code, "CUTPOINTS_PER_FIELD_EXCEEDED")

    def test_column_iterator_stops_after_limit_plus_one(self):
        def columns():
            for index in range(17):
                yield ColumnPlan("c" + str(index), "numeric")
            raise OverReadError("Constructor read beyond the seventeen-column sentinel")
        with self.assertRaises(DriftError) as caught:
            ComparisonPlan(columns())
        self.assertEqual(caught.exception.status, "UNKNOWN_BUDGET")
        self.assertEqual(caught.exception.reason_code, "SELECTED_FIELDS_EXCEEDED")

    def test_categorical_cutpoints_stop_at_first_value(self):
        def values():
            yield 1
            raise OverReadError("Categorical constructor need not read a second cutpoint")
        with self.assertRaises(DriftError) as caught:
            ColumnPlan("x", "categorical", values())
        self.assertEqual(caught.exception.status, "INVALID_INPUT")
        self.assertEqual(caught.exception.reason_code, "CATEGORICAL_CUTPOINTS_FORBIDDEN")
