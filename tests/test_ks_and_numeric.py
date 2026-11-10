"""Independent tests for the counted-CDF KS sweep, right-closed bins, and smoothed PSI."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import math
import unittest

import pyarrow as pa
from scipy.stats import ks_2samp

from tabular_drift_audit import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_INSUFFICIENT_NONMISSING,
    ColumnPlan,
    ComparisonPlan,
    ExactFraction,
    compare_tables,
)
from tests.reference_oracles import (
    decimal_psi_from_counts,
    decimal_right_closed_bin_counts,
    direct_cdf_ks_fraction,
    generate_nonempty_multisets,
)


class TestKSAndNumericMetrics(unittest.TestCase):
    """Verify counted-CDF KS sweep and smoothed PSI against independent Fraction/Decimal oracles."""

    def test_exhaustive_1156_ordered_multiset_pairs(self) -> None:
        multisets = generate_nonempty_multisets((-1, 0, 1), max_len=4)
        self.assertEqual(len(multisets), 34)
        plan = ComparisonPlan([ColumnPlan("x", "numeric", cutpoints=(-0.5, 0.5))])

        pair_count = 0
        for ref_ms in multisets:
            for cur_ms in multisets:
                pair_count += 1
                ref_table = pa.table({"x": pa.array(ref_ms, type=pa.int64())})
                cur_table = pa.table({"x": pa.array(cur_ms, type=pa.int64())})
                report = compare_tables(ref_table, cur_table, plan)
                num_rep = report.column("x").numeric
                self.assertIsNotNone(num_rep)
                assert num_rep is not None
                self.assertEqual(num_rep.availability, AVAILABILITY_AVAILABLE)
                assert num_rep.ks_distance is not None

                expected_frac = direct_cdf_ks_fraction(ref_ms, cur_ms)
                self.assertEqual(num_rep.ks_distance.numerator, expected_frac.numerator)
                self.assertEqual(num_rep.ks_distance.denominator, expected_frac.denominator)
                self.assertAlmostEqual(
                    num_rep.ks_distance.value, float(expected_frac), places=15
                )

                common_denom = len(ref_ms) * len(cur_ms)
                self.assertLessEqual(common_denom, 268_435_456)
                self.assertGreaterEqual(num_rep.ks_distance.numerator, 0)
                self.assertLessEqual(
                    num_rep.ks_distance.numerator, num_rep.ks_distance.denominator
                )
                self.assertLessEqual(num_rep.ks_distance.denominator, common_denom)
                self.assertEqual(
                    math.gcd(
                        num_rep.ks_distance.numerator, num_rep.ks_distance.denominator
                    ),
                    1,
                )

        self.assertEqual(pair_count, 1156)

    def test_ks_exact_goldens_and_scipy_statistic(self) -> None:
        cases = [
            ([0.0, 1.0], [0.0, 1.0], Fraction(0, 1)),
            ([0.0, 1.0], [2.0, 3.0], Fraction(1, 1)),
            ([0.0, 0.0, 1.0, 1.0], [0.0, 1.0, 1.0, 1.0], Fraction(1, 4)),
            ([0.0, 0.0, 2.0], [0.0, 1.0], Fraction(1, 3)),
        ]
        plan = ComparisonPlan([ColumnPlan("val", "numeric", cutpoints=(0.0, 1.0))])
        for ref_vals, cur_vals, expected in cases:
            ref_table = pa.table({"val": pa.array(ref_vals, type=pa.float64())})
            cur_table = pa.table({"val": pa.array(cur_vals, type=pa.float64())})
            report = compare_tables(ref_table, cur_table, plan)
            num_rep = report.column("val").numeric
            assert num_rep is not None and num_rep.ks_distance is not None

            oracle = direct_cdf_ks_fraction(ref_vals, cur_vals)
            self.assertEqual(oracle, expected)
            self.assertEqual(num_rep.ks_distance.to_fraction(), expected)
            self.assertAlmostEqual(num_rep.ks_distance.value, float(expected), places=15)

            scipy_stat = float(ks_2samp(ref_vals, cur_vals).statistic)
            self.assertAlmostEqual(num_rep.ks_distance.value, scipy_stat, places=15)

    def test_ks_signed_zero_and_float_ties(self) -> None:
        plan = ComparisonPlan([ColumnPlan("x", "numeric", cutpoints=(0.0,))])
        ref_table = pa.table({"x": pa.array([-0.0, -0.0, 0.0, 1.5, 1.5], type=pa.float64())})
        cur_table = pa.table({"x": pa.array([0.0, 0.0, 0.0, 1.5, 2.5], type=pa.float64())})
        report = compare_tables(ref_table, cur_table, plan)
        num_rep = report.column("x").numeric
        assert num_rep is not None and num_rep.ks_distance is not None

        self.assertEqual(num_rep.ks_distance.to_fraction(), Fraction(1, 5))
        self.assertEqual(num_rep.ks_sweep_points, 3)
        self.assertEqual(num_rep.reference_bin_counts, (3, 2))
        self.assertEqual(num_rep.current_bin_counts, (3, 2))

        ref_zeros = pa.table({"x": pa.array([-0.0, -0.0], type=pa.float64())})
        cur_zeros = pa.table({"x": pa.array([0.0, 0.0], type=pa.float64())})
        zero_rep = compare_tables(ref_zeros, cur_zeros, plan).column("x").numeric
        assert zero_rep is not None and zero_rep.ks_distance is not None
        self.assertEqual(zero_rep.ks_distance.to_fraction(), Fraction(0, 1))
        self.assertEqual(zero_rep.ks_sweep_points, 1)
        self.assertEqual(zero_rep.psi, 0.0)

    def test_frozen_bin_receipts_and_decimal_psi_goldens(self) -> None:
        plan = ComparisonPlan([ColumnPlan("x", "numeric", cutpoints=(0.0, 1.0))])
        vals = [-1.0, 0.0, 1.0, 2.0]
        expected_bins = decimal_right_closed_bin_counts(vals, [0, 1])
        self.assertEqual(expected_bins, [2, 1, 1])

        ref_table = pa.table({"x": pa.array(vals, type=pa.float64())})
        cur_table = pa.table({"x": pa.array([0.0, 0.5, 1.0, 10.0], type=pa.float64())})
        report = compare_tables(ref_table, cur_table, plan)
        num_rep = report.column("x").numeric
        assert num_rep is not None
        self.assertEqual(num_rep.reference_bin_counts, (2, 1, 1))
        self.assertEqual(num_rep.current_bin_counts, (1, 2, 1))
        self.assertEqual(num_rep.smoothing_alpha, ExactFraction(1, 2))

        psi_plan = ComparisonPlan([ColumnPlan("x", "numeric", cutpoints=(0.0,))])
        ref_psi = pa.table({"x": pa.array([-1.0, 0.0], type=pa.float64())})
        cur_psi = pa.table({"x": pa.array([1.0, 2.0], type=pa.float64())})
        psi_rep = compare_tables(ref_psi, cur_psi, psi_plan).column("x").numeric
        assert psi_rep is not None and psi_rep.psi is not None
        self.assertEqual(psi_rep.reference_bin_counts, (2, 0))
        self.assertEqual(psi_rep.current_bin_counts, (0, 2))

        with localcontext() as ctx:
            ctx.prec = 70
            golden_str = "2.145917216578800499467679110968250186034135139024690295883530521965571"
            formula_val = (Decimal(4) / Decimal(3)) * Decimal(5).ln()
            oracle_val = decimal_psi_from_counts([2, 0], [0, 2])
            self.assertEqual(str(formula_val), golden_str)
            # Independently rounded Decimal formulas may differ in their final
            # (70th) digit. Require agreement to65 digits, not spelling identity.
            self.assertLess(abs(oracle_val - Decimal(golden_str)), Decimal("1e-65"))
            self.assertAlmostEqual(psi_rep.psi, float(Decimal(golden_str)), places=15)

    def test_psi_sample_size_dependent_smoothing_and_all_null_receipt(self) -> None:
        plan = ComparisonPlan([ColumnPlan("x", "numeric", cutpoints=(0.0,))])
        small_ref = pa.table({"x": pa.array([-1.0, 0.0], type=pa.float64())})
        small_cur = pa.table({"x": pa.array([1.0, 2.0], type=pa.float64())})
        large_ref = pa.table({"x": pa.array([-1.0] * 20 + [0.0] * 20, type=pa.float64())})
        large_cur = pa.table({"x": pa.array([1.0] * 20 + [2.0] * 20, type=pa.float64())})

        small_rep = compare_tables(small_ref, small_cur, plan).column("x").numeric
        large_rep = compare_tables(large_ref, large_cur, plan).column("x").numeric
        assert small_rep is not None and large_rep is not None
        assert small_rep.psi is not None and large_rep.psi is not None

        self.assertNotAlmostEqual(small_rep.psi, large_rep.psi, places=3)
        self.assertGreater(large_rep.psi, small_rep.psi)
        self.assertAlmostEqual(
            large_rep.psi, float(decimal_psi_from_counts([40, 0], [0, 40])), places=14
        )

        all_null_ref = pa.table({"x": pa.array([None, float("nan")], type=pa.float64())})
        all_null_rep = compare_tables(all_null_ref, small_cur, plan).column("x").numeric
        assert all_null_rep is not None
        self.assertEqual(all_null_rep.availability, AVAILABILITY_INSUFFICIENT_NONMISSING)
        self.assertEqual(all_null_rep.reference_bin_counts, (0, 0))
        self.assertEqual(all_null_rep.current_bin_counts, (0, 2))
        self.assertIsNone(all_null_rep.ks_distance)
        self.assertIsNone(all_null_rep.psi)


if __name__ == "__main__":
    unittest.main()
