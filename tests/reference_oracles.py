"""Independent test-only reference evaluators using Fraction and 70-digit Decimal.

This module does not import production metric implementations.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext
from fractions import Fraction
import itertools
import math


def direct_cdf_ks_fraction(reference: Sequence[float | int], current: Sequence[float | int]) -> Fraction:
    """Directly evaluate empirical CDFs with exact Fraction at every observed union value."""
    ref_clean = [0.0 if float(x) == 0.0 else float(x) for x in reference if x is not None and not math.isnan(float(x))]
    cur_clean = [0.0 if float(x) == 0.0 else float(x) for x in current if x is not None and not math.isnan(float(x))]
    n_r = len(ref_clean)
    n_c = len(cur_clean)
    if n_r == 0 or n_c == 0:
        raise ValueError("Both samples must be non-empty for direct KS evaluation.")

    union_points = sorted(set(ref_clean) | set(cur_clean))
    max_diff = Fraction(0, 1)
    for t in union_points:
        count_r = sum(1 for x in ref_clean if x <= t)
        count_c = sum(1 for x in cur_clean if x <= t)
        diff = abs(Fraction(count_r, n_r) - Fraction(count_c, n_c))
        if diff > max_diff:
            max_diff = diff
    return max_diff


def generate_nonempty_multisets(
    alphabet: Sequence[int] = (-1, 0, 1),
    max_len: int = 4,
) -> list[tuple[int, ...]]:
    """Generate all 34 non-empty multisets of lengths 1..4 from {-1, 0, 1}."""
    multisets: list[tuple[int, ...]] = []
    for length in range(1, max_len + 1):
        for combo in itertools.combinations_with_replacement(alphabet, length):
            multisets.append(combo)
    return multisets


def decimal_right_closed_bin_counts(
    values: Sequence[float | int | Decimal],
    cutpoints: Sequence[float | int | Decimal],
) -> list[int]:
    """Assign values to (-inf, c0], (c0, c1], ..., (c_last, +inf) using Decimal comparisons."""
    with localcontext() as ctx:
        ctx.prec = 70
        cuts = [Decimal(str(c)) for c in cutpoints]
        counts = [0] * (len(cuts) + 1)
        for v in values:
            dv = Decimal(str(v))
            placed = False
            for idx, cut in enumerate(cuts):
                if dv <= cut:
                    counts[idx] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1
        return counts


def decimal_psi_from_counts(
    ref_counts: Sequence[int],
    cur_counts: Sequence[int],
) -> Decimal:
    """Compute 70-digit Decimal PSI with additive alpha = 1/2 smoothing."""
    if len(ref_counts) != len(cur_counts) or len(ref_counts) == 0:
        raise ValueError("Bin count vectors must have matching non-zero length.")
    with localcontext() as ctx:
        ctx.prec = 70
        k_bins = Decimal(len(ref_counts))
        half = Decimal(1) / Decimal(2)
        n_r = Decimal(sum(ref_counts))
        n_c = Decimal(sum(cur_counts))
        denom_r = n_r + k_bins * half
        denom_c = n_c + k_bins * half
        total = Decimal(0)
        for r_i, c_i in zip(ref_counts, cur_counts):
            p_i = (Decimal(r_i) + half) / denom_r
            q_i = (Decimal(c_i) + half) / denom_c
            total += (p_i - q_i) * (p_i / q_i).ln()
        return total


def decimal_jsd_bits_from_labels(
    reference_labels: Sequence[str],
    current_labels: Sequence[str],
) -> Decimal:
    """Compute 70-digit Decimal JSD in bits over sorted UTF-8 category keys."""
    with localcontext() as ctx:
        ctx.prec = 70
        ref_counts: dict[str, int] = {}
        cur_counts: dict[str, int] = {}
        for s in reference_labels:
            ref_counts[s] = ref_counts.get(s, 0) + 1
        for s in current_labels:
            cur_counts[s] = cur_counts.get(s, 0) + 1

        sorted_keys = sorted(
            set(ref_counts.keys()) | set(cur_counts.keys()),
            key=lambda k: k.encode("utf-8"),
        )
        n_r = Decimal(len(reference_labels))
        n_c = Decimal(len(current_labels))
        ln2 = Decimal(2).ln()
        half = Decimal(1) / Decimal(2)
        total_bits = Decimal(0)

        for key in sorted_keys:
            p_i = Decimal(ref_counts.get(key, 0)) / n_r
            q_i = Decimal(cur_counts.get(key, 0)) / n_c
            m_i = (p_i + q_i) * half
            term = Decimal(0)
            if p_i > 0:
                term += p_i * ((p_i / m_i).ln() / ln2)
            if q_i > 0:
                term += q_i * ((q_i / m_i).ln() / ln2)
            total_bits += half * term
        return total_bits
