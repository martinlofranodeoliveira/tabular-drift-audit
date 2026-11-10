"""Independent categorical/schema goldens and typed admission regressions."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
from fractions import Fraction
import json
import math
import struct
import unittest
from unittest.mock import patch

import numpy as np
import pyarrow as pa
from scipy.spatial.distance import jensenshannon

from tabular_drift_audit import Budget, ColumnPlan, ComparisonPlan, DriftError, compare_tables
from tests.reference_oracles import decimal_jsd_bits_from_labels


def numeric(values, name="x"):
    return pa.table({name: pa.array(values, type=pa.float64())})


class TestCategoriesSchemaAndBudgets(unittest.TestCase):
    def typed_failure(self, status, code, operation):
        with self.assertRaises(DriftError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(caught.exception.reason_code, code)

    def test_categorical_independent_literals_and_dictionary_identity(self):
        plan = ComparisonPlan([ColumnPlan("category", "categorical")])
        for ref, cur, expected in [(["a"], ["b"], 1.0), (["a", "b"], ["a", "b"], 0.0),
                                   (["a", "a", "b", "b"], ["a"] * 4, 0.31127812445913286)]:
            report = compare_tables(pa.table({"category": ref}), pa.table({"category": cur}), plan)
            actual = report.column("category").categorical.js_divergence_bits
            self.assertAlmostEqual(actual, expected, places=14)
            self.assertAlmostEqual(actual, float(decimal_jsd_bits_from_labels(ref, cur)), places=14)
        left = pa.DictionaryArray.from_arrays(pa.array([0, 1, 0, None], type=pa.int8()),
                                             pa.array(["a", "b", "unused"]))
        right = pa.DictionaryArray.from_arrays(pa.array([1, 0, 1, None], type=pa.int32()),
                                              pa.array(["b", "a"]))
        result = compare_tables(pa.table({"category": left}), pa.table({"category": right}), plan)
        self.assertEqual(result.column("category").categorical.js_divergence_bits, 0.0)
        self.assertEqual(result.column("category").categorical.union_cardinality, 2)
        self.assertIn("category", result.schema.compatible_physical_changes)
        self.assertNotEqual(result.column("category").reference_physical_type,
                            result.column("category").current_physical_type)
        self.assertAlmostEqual(jensenshannon([.5, .5], [1., 0.], base=2)**2,
                               0.31127812445913286, places=14)

    def test_unicode_no_normalization_and_no_label_export(self):
        labels = ["RAW-LABEL-NFC-é", "RAW-LABEL-NFD-e\u0301"]
        plan = ComparisonPlan([ColumnPlan("category", "categorical")])
        report = compare_tables(pa.table({"category": labels}),
                                pa.table({"category": [labels[0], labels[0]]}), plan)
        self.assertEqual(report.column("category").categorical.union_cardinality, 2)
        self.assertAlmostEqual(report.column("category").categorical.js_divergence_bits,
                               0.31127812445913286, places=14)
        for value in labels:
            self.assertNotIn(value, report.to_json())
            self.assertNotIn(json.dumps(value)[1:-1], report.to_json())

    def test_missing_rates_survive_distribution_schema_mismatch(self):
        reference = pa.table({"num": pa.array([None, float("nan"), 1., 2.]),
                              "cat": pa.array(["a", None, "NaN", ""]), "old": [1, 2, 3, 4]})
        current = pa.table({"num": ["x", None, "", "NaN"],
                            "cat": pa.array([None, None, "NaN", ""], type=pa.large_string()),
                            "new": [1, 2, 3, 4]})
        plan = ComparisonPlan([ColumnPlan("num", "numeric"), ColumnPlan("cat", "categorical"),
                               ColumnPlan("absent", "numeric")])
        report = compare_tables(reference, current, plan)
        self.assertEqual(report.schema.added_fields, ("new",))
        self.assertEqual(report.schema.dropped_fields, ("old",))
        self.assertIn("num", report.schema.incompatible_type_changes)
        self.assertIn("cat", report.schema.compatible_physical_changes)
        self.assertEqual(report.column("num").numeric.availability, "UNAVAILABLE_SCHEMA")
        missing = report.column("num").missingness
        self.assertEqual((missing.reference_missing_count, missing.current_missing_count), (2, 1))
        self.assertEqual(missing.missing_rate_delta.to_fraction(), Fraction(-1, 4))
        self.assertIsNone(report.column("absent").missingness.reference_missing_count)
        self.assertIsNone(report.column("absent").missingness.missing_rate_delta)
        self.assertEqual(report.column("cat").missingness.missing_rate_delta.to_fraction(), Fraction(1, 4))
        mismatched_both = compare_tables(pa.table({"x": ["a"]}), pa.table({"x": ["a"]}),
                                         ComparisonPlan([ColumnPlan("x", "numeric")]))
        self.assertEqual(mismatched_both.column("x").schema_status, "PLAN_KIND_MISMATCH")
        self.assertEqual(mismatched_both.schema.incompatible_type_changes, ())

    def test_empty_and_all_missing_are_not_numeric_zero(self):
        plan = ComparisonPlan([ColumnPlan("x", "numeric")])
        report = compare_tables(numeric([]), numeric([None, float("nan")]), plan)
        col = report.column("x")
        self.assertEqual(col.availability, "INSUFFICIENT_NONMISSING")
        self.assertIsNone(col.numeric.ks_distance)
        self.assertIsNone(col.missingness.reference_missing_rate)
        self.assertEqual(col.missingness.current_missing_rate.to_fraction(), Fraction(1))
        self.assertIsNone(col.missingness.missing_rate_delta)

    def test_invariance_nonmutation_and_scalar_ks_bound(self):
        names = [ColumnPlan("z", "categorical"), ColumnPlan("x", "numeric", [0])]
        plan = ComparisonPlan(names)
        raw = np.array([0., 1., 2.])
        reference = pa.table({"x": raw, "z": ["a", "b", "a"]})
        current = pa.table({"z": ["b", "a", "b"], "x": [1., 2., 3.]})
        before = raw.copy()
        report = compare_tables(reference, current, plan)
        serialized = report.to_json_bytes()
        self.assertTrue(np.array_equal(raw, before))
        permutation = pa.array([2, 0, 1])
        permuted = compare_tables(reference.take(permutation), current.take(permutation), plan)
        self.assertEqual(serialized, permuted.to_json_bytes())
        exported = report.to_dict(); exported["complete"] = False
        raw[0] = 7.; names.clear()
        self.assertEqual(report.to_json_bytes(), serialized)
        self.assertTrue(report.to_dict()["complete"])
        with self.assertRaises(FrozenInstanceError):
            plan.columns = ()
        extreme = compare_tables(numeric([-1e12] * 16384), numeric([1e12] * 16384),
                                  ComparisonPlan([ColumnPlan("x", "numeric")]))
        self.assertEqual(extreme.column("x").numeric.ks_distance.to_fraction(), Fraction(1))

    def test_typed_source_invalidity_and_unused_dictionary_values(self):
        plan = ComparisonPlan([ColumnPlan("x", "numeric")])
        self.typed_failure("INVALID_INPUT", "NUMERIC_INFINITY", lambda: compare_tables(
            pa.table({"x": [1.], "unused": [float("inf")]}), numeric([1]), plan))
        self.typed_failure("UNSUPPORTED_INPUT", "BOOLEAN_TYPE_UNSUPPORTED", lambda: compare_tables(
            pa.table({"x": [1.], "unused": [True]}), numeric([1]), plan))
        dictionary = pa.DictionaryArray.from_arrays(pa.array([0]), pa.array(["a", "x" * 257]))
        catplan = ComparisonPlan([ColumnPlan("x", "categorical")])
        self.typed_failure("UNKNOWN_BUDGET", "CATEGORY_BYTES_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": dictionary}), pa.table({"x": ["a"]}), catplan))
        self.typed_failure("UNKNOWN_BUDGET", "CATEGORY_BYTES_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": ["é" * 129]}), pa.table({"x": ["a"]}), catplan))
        bad = pa.Array.from_buffers(pa.string(), 1, [None, pa.py_buffer(struct.pack("<ii", 0, 1)),
                                                   pa.py_buffer(b"\xff")])
        self.typed_failure("INVALID_INPUT", "INVALID_UTF8_STRING", lambda: compare_tables(
            pa.table({"x": bad}), pa.table({"x": ["a"]}), catplan))
        metadata_schema = pa.schema([pa.field("x", pa.float64(), metadata={b"ARROW:extension:name": b"fixture"})])
        self.typed_failure("UNSUPPORTED_INPUT", "EXTENSION_METADATA_UNSUPPORTED", lambda: compare_tables(
            pa.Table.from_arrays([pa.array([1.])], schema=metadata_schema), numeric([1]), plan))

    def test_each_core_budget_seam_and_global_work(self):
        plan = ComparisonPlan([ColumnPlan("x", "numeric", [0, 1])])
        ref = numeric([0, 1]); cur = numeric([1, 2])
        for budget, code in [
            (Budget(max_rows_per_dataset=1), "ROWS_PER_DATASET_BUDGET_EXCEEDED"),
            (Budget(max_cutpoints_per_field=1), "CUTPOINTS_PER_FIELD_EXCEEDED"),
            (Budget(max_retained_arrow_bytes=1), "RETAINED_ARROW_BYTES_EXCEEDED"),
            (Budget(max_projected_batch_bytes=4), "PROJECTED_BATCH_BYTES_EXCEEDED"),
            (Budget(max_value_visits=1), "VALUE_VISIT_BUDGET_EXCEEDED"),
            (Budget(max_ks_sweep_steps=1), "KS_SWEEP_BUDGET_EXCEEDED"),
            (Budget(max_report_bytes=1), "REPORT_BYTES_BUDGET_EXCEEDED"),
        ]:
            with self.subTest(code=code):
                self.typed_failure("UNKNOWN_BUDGET", code, lambda: compare_tables(ref, cur, plan, budget))
        category_plan = ComparisonPlan([ColumnPlan("x", "categorical")])
        self.typed_failure("UNKNOWN_BUDGET", "CATEGORY_UNION_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": ["a"]}), pa.table({"x": ["b"]}), category_plan,
            Budget(max_category_union_per_field=1)))
        dictionary = pa.DictionaryArray.from_arrays(pa.array([0]), pa.array(["a", "unused"]))
        self.typed_failure("UNKNOWN_BUDGET", "DICTIONARY_ENTRY_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": dictionary}), pa.table({"x": ["a"]}), category_plan,
            Budget(max_dictionary_entries_per_field=1)))
        empty_chunks = pa.chunked_array([pa.array([], type=pa.float64())] * 100)
        self.typed_failure("UNKNOWN_BUDGET", "VALUE_VISIT_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": empty_chunks}), numeric([]), plan, Budget(max_value_visits=10)))
        two = pa.table({"x": [0., 1.], "y": [0., 1.]})
        two_plan = ComparisonPlan([ColumnPlan("x", "numeric"), ColumnPlan("y", "numeric")])
        self.typed_failure("UNKNOWN_BUDGET", "KS_SWEEP_BUDGET_EXCEEDED", lambda: compare_tables(
            two, two, two_plan, Budget(max_ks_sweep_steps=3)))
        self.assertEqual(compare_tables(ref, cur, plan, Budget(max_batch_rows=1)).to_json_bytes(),
                         compare_tables(ref, cur, plan).to_json_bytes())
        self.typed_failure("UNKNOWN_BUDGET", "KS_FIELD_UNION_POINTS_EXCEEDED", lambda: compare_tables(
            ref, cur, plan, Budget(max_ks_union_points_per_field=1)))
        self.typed_failure("UNKNOWN_BUDGET", "SOURCE_FIELDS_BUDGET_EXCEEDED", lambda: compare_tables(
            two, two, two_plan, Budget(max_source_fields_per_dataset=1)))
        self.typed_failure("UNKNOWN_BUDGET", "SELECTED_FIELDS_EXCEEDED", lambda: compare_tables(
            two, two, two_plan, Budget(max_selected_fields=1)))
        long_name = numeric([1], "long")
        self.typed_failure("UNKNOWN_BUDGET", "FIELD_NAME_BYTES_EXCEEDED", lambda: compare_tables(
            long_name, long_name, ComparisonPlan([ColumnPlan("long", "numeric")]), Budget(max_field_name_bytes=1)))
        self.typed_failure("UNKNOWN_BUDGET", "CATEGORY_BYTES_BUDGET_EXCEEDED", lambda: compare_tables(
            pa.table({"x": ["é"]}), pa.table({"x": ["a"]}), category_plan,
            Budget(max_category_utf8_bytes=1)))

    def test_operational_arrow_memory_error_is_not_algorithmic(self):
        plan = ComparisonPlan([ColumnPlan("x", "categorical")])
        with patch("tabular_drift_audit.core.pc.binary_length", side_effect=pa.ArrowMemoryError("safe seam")):
            with self.assertRaises(pa.ArrowMemoryError):
                compare_tables(pa.table({"x": ["a"]}), pa.table({"x": ["b"]}), plan)

    def test_pre_response_sample_size_psi_golden(self):
        plan = ComparisonPlan([ColumnPlan("x", "numeric", [0])])
        result = compare_tables(numeric([-1, 1, 1, 1]), numeric([-1, -1, 1, 1, 1, 1, 1, 1]), plan)
        expected = Decimal("0.002404746325338505505391568929299487562001609305168392395148928953093331")
        self.assertAlmostEqual(result.column("x").numeric.psi, float(expected), places=14)
        self.assertGreater(result.column("x").numeric.psi, 0)
