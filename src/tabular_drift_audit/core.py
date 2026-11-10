"""Core statistical, schema, missingness, and budget engine for Tabular Drift Audit.

Copyright (c) 2026 Lofrano Tecnologia (https://lofranotecnologia.com.br)
GitHub owner: martinlofranodeoliveira
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from scipy.special import rel_entr

from tabular_drift_audit.errors import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_INSUFFICIENT_NONMISSING,
    AVAILABILITY_UNAVAILABLE_SCHEMA,
    STATUS_INVALID_INPUT,
    STATUS_NUMERICALLY_INDETERMINATE,
    STATUS_UNKNOWN_BUDGET,
    STATUS_UNSUPPORTED_INPUT,
    DriftError,
)
from tabular_drift_audit.plan import (
    DEFAULT_BUDGET,
     NUMERIC_ABS_LIMIT,
    Budget,
    CategoricalMetricReport,
    ColumnPlan,
    ColumnReport,
    ComparisonPlan,
    ComparisonReport,
    ExactFraction,
    FieldSchemaComparison,
    LogicalWorkReport,
    MissingnessReport,
    NumericMetricReport,
    SchemaReport,
    validate_ascii_field_name,
)

_ENDPOINT_SNAP_TOLERANCE = 1e-14
_LN_2 = math.log(2.0)
_SMOOTHING_POLICY_NAME = "additive_half_count"
_SMOOTHING_ALPHA = ExactFraction(1, 2)


class WorkLedger:
    """Operation-wide work ledger charging value visits and KS sweep steps before growth."""

    __slots__ = ("_max_value_visits", "_max_ks_sweep_steps", "_value_visits", "_ks_sweep_steps")

    def __init__(self, budget: Budget) -> None:
        self._max_value_visits = budget.max_value_visits
        self._max_ks_sweep_steps = budget.max_ks_sweep_steps
        self._value_visits = 0
        self._ks_sweep_steps = 0

    @property
    def value_visits(self) -> int:
        return self._value_visits

    @property
    def ks_sweep_steps(self) -> int:
        return self._ks_sweep_steps

    def charge_visits(self, count: int) -> None:
        if count < 0:
            raise ValueError("Visit count cannot be negative.")
        if self._value_visits + count > self._max_value_visits:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "VALUE_VISIT_BUDGET_EXCEEDED",
                f"Operation value visits ({self._value_visits + count}) exceed limit {self._max_value_visits}.",
            )
        self._value_visits += count

    def charge_ks_step(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("KS step count cannot be negative.")
        if self._ks_sweep_steps + count > self._max_ks_sweep_steps:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "KS_SWEEP_BUDGET_EXCEEDED",
                f"Operation KS union sweep steps ({self._ks_sweep_steps + count}) exceed limit {self._max_ks_sweep_steps}.",
            )
        self._ks_sweep_steps += count


@dataclass(frozen=True, slots=True)
class _SourceFieldSpec:
    name: str
    physical_type: str
    logical_kind: str
    arrow_type: pa.DataType


@dataclass(slots=True)
class _ScannedFieldData:
    spec: _SourceFieldSpec
    total_rows: int
    missing_count: int
    nonmissing_count: int
    numeric_values: list[float] | None
    categorical_values: list[str] | None


def _has_extension_metadata(metadata: dict[bytes, bytes] | None) -> bool:
    if not metadata:
        return False
    for key in metadata:
        if key.startswith(b"ARROW:extension"):
            return True
    return False


def _classify_arrow_field(field: pa.Field, budget: Budget) -> _SourceFieldSpec:
    name = validate_ascii_field_name(field.name, budget.max_field_name_bytes)
    if _has_extension_metadata(field.metadata):
        raise DriftError(
            STATUS_UNSUPPORTED_INPUT,
            "EXTENSION_METADATA_UNSUPPORTED",
            f"Field {name!r} carries unsupported ARROW:extension metadata.",
        )
    dtype = field.type
    if isinstance(dtype, pa.BaseExtensionType):
        raise DriftError(
            STATUS_UNSUPPORTED_INPUT,
            "EXTENSION_TYPE_UNSUPPORTED",
            f"Field {name!r} uses an unsupported Arrow extension type {dtype}.",
        )
    if pa.types.is_null(dtype):
        raise DriftError(
            STATUS_UNSUPPORTED_INPUT,
            "UNTYPED_NULL_COLUMN_UNSUPPORTED",
            f"Field {name!r} has pa.null() type; all-missing columns require an explicit admitted type.",
        )
    if pa.types.is_boolean(dtype):
        raise DriftError(
            STATUS_UNSUPPORTED_INPUT,
            "BOOLEAN_TYPE_UNSUPPORTED",
            f"Field {name!r} uses unsupported boolean type.",
        )
    if pa.types.is_int32(dtype):
        return _SourceFieldSpec(
            name=name, physical_type="int32", logical_kind="numeric", arrow_type=dtype
        )
    if pa.types.is_int64(dtype):
        return _SourceFieldSpec(
            name=name, physical_type="int64", logical_kind="numeric", arrow_type=dtype
        )
    if pa.types.is_float64(dtype):
        return _SourceFieldSpec(
            name=name, physical_type="float64", logical_kind="numeric", arrow_type=dtype
        )
    if pa.types.is_string(dtype):
        return _SourceFieldSpec(
            name=name, physical_type="string", logical_kind="categorical", arrow_type=dtype
        )
    if pa.types.is_large_string(dtype):
        return _SourceFieldSpec(
            name=name,
            physical_type="large_string",
            logical_kind="categorical",
            arrow_type=dtype,
        )
    if pa.types.is_dictionary(dtype):
        dict_type: pa.DictionaryType = dtype
        val_type = dict_type.value_type
        idx_type = dict_type.index_type
        if isinstance(val_type, pa.BaseExtensionType):
            raise DriftError(
                STATUS_UNSUPPORTED_INPUT,
                "EXTENSION_TYPE_UNSUPPORTED",
                f"Dictionary field {name!r} has an extension value type.",
            )
        if not pa.types.is_integer(idx_type) or not (
            pa.types.is_string(val_type) or pa.types.is_large_string(val_type)
        ):
            raise DriftError(
                STATUS_UNSUPPORTED_INPUT,
                "UNSUPPORTED_DICTIONARY_TYPE",
                f"Field {name!r} has unsupported dictionary type {dtype}.",
            )
        return _SourceFieldSpec(
            name=name,
            physical_type=str(dtype),
            logical_kind="categorical",
            arrow_type=dtype,
        )
    raise DriftError(
        STATUS_UNSUPPORTED_INPUT,
        "UNSUPPORTED_ARROW_TYPE",
        f"Field {name!r} has unsupported Arrow type {dtype}.",
    )


def _validate_source_schema(schema: pa.Schema, row_count: int, label: str, budget: Budget) -> dict[str, _SourceFieldSpec]:
    if len(schema) > budget.max_source_fields_per_dataset:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "SOURCE_FIELDS_BUDGET_EXCEEDED")
    if row_count > budget.max_rows_per_dataset:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "ROWS_PER_DATASET_BUDGET_EXCEEDED")
    if _has_extension_metadata(schema.metadata):
        raise DriftError(
            STATUS_UNSUPPORTED_INPUT,
            "EXTENSION_METADATA_UNSUPPORTED",
            f"{label} schema carries unsupported ARROW:extension metadata.",
        )
    specs: dict[str, _SourceFieldSpec] = {}
    for idx in range(len(schema)):
        field = schema.field(idx)
        spec = _classify_arrow_field(field, budget)
        if spec.name in specs:
            raise DriftError(
                STATUS_INVALID_INPUT,
                "DUPLICATE_SOURCE_FIELD_NAME",
                f"{label} table contains duplicate field name {spec.name!r}.",
            )
        specs[spec.name] = spec

    return specs


def _validate_table_schema(table: pa.Table, label: str, budget: Budget) -> dict[str, _SourceFieldSpec]:
    return _validate_source_schema(table.schema, table.num_rows, label, budget)


def _build_schema_report(
    ref_specs: dict[str, _SourceFieldSpec],
    cur_specs: dict[str, _SourceFieldSpec],
) -> SchemaReport:
    ref_names = tuple(sorted(ref_specs.keys(), key=lambda s: s.encode("ascii")))
    cur_names = tuple(sorted(cur_specs.keys(), key=lambda s: s.encode("ascii")))
    union_names = tuple(
        sorted(set(ref_specs.keys()) | set(cur_specs.keys()), key=lambda s: s.encode("ascii"))
    )

    common_fields: list[str] = []
    added_fields: list[str] = []
    dropped_fields: list[str] = []
    compatible_physical_changes: list[str] = []
    incompatible_type_changes: list[str] = []
    field_entries: list[FieldSchemaComparison] = []

    for name in union_names:
        r_spec = ref_specs.get(name)
        c_spec = cur_specs.get(name)
        if r_spec is not None and c_spec is not None:
            common_fields.append(name)
            phys_changed = r_spec.physical_type != c_spec.physical_type
            logical_changed = r_spec.logical_kind != c_spec.logical_kind
            compatible = not logical_changed
            if logical_changed:
                status = "INCOMPATIBLE_TYPE_CHANGE"
                incompatible_type_changes.append(name)
            elif phys_changed:
                status = "COMPATIBLE_PHYSICAL_CHANGE"
                compatible_physical_changes.append(name)
            else:
                status = "IDENTICAL"
            field_entries.append(
                FieldSchemaComparison(
                    name=name,
                    present_in_reference=True,
                    present_in_current=True,
                    reference_physical_type=r_spec.physical_type,
                    current_physical_type=c_spec.physical_type,
                    reference_logical_kind=r_spec.logical_kind,
                    current_logical_kind=c_spec.logical_kind,
                    physical_type_changed=phys_changed,
                    logical_type_changed=logical_changed,
                    compatible_for_metrics=compatible,
                    status=status,
                )
            )
        elif r_spec is not None:
            dropped_fields.append(name)
            field_entries.append(
                FieldSchemaComparison(
                    name=name,
                    present_in_reference=True,
                    present_in_current=False,
                    reference_physical_type=r_spec.physical_type,
                    current_physical_type=None,
                    reference_logical_kind=r_spec.logical_kind,
                    current_logical_kind=None,
                    physical_type_changed=False,
                    logical_type_changed=False,
                    compatible_for_metrics=False,
                    status="DROPPED",
                )
            )
        else:
            assert c_spec is not None
            added_fields.append(name)
            field_entries.append(
                FieldSchemaComparison(
                    name=name,
                    present_in_reference=False,
                    present_in_current=True,
                    reference_physical_type=None,
                    current_physical_type=c_spec.physical_type,
                    reference_logical_kind=None,
                    current_logical_kind=c_spec.logical_kind,
                    physical_type_changed=False,
                    logical_type_changed=False,
                    compatible_for_metrics=False,
                    status="ADDED",
                )
            )

    return SchemaReport(
        reference_field_names=ref_names,
        current_field_names=cur_names,
        common_fields=tuple(common_fields),
        added_fields=tuple(added_fields),
        dropped_fields=tuple(dropped_fields),
        compatible_physical_changes=tuple(compatible_physical_changes),
        incompatible_type_changes=tuple(incompatible_type_changes),
        fields=tuple(field_entries),
    )


def _validate_utf8_string_value(val: str, field_name: str, max_utf8_bytes: int) -> None:
    try:
        encoded = val.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_UTF8_STRING",
            f"Field {field_name!r} contains invalid UTF-8 data.",
        ) from exc
    if len(encoded) > max_utf8_bytes:
        raise DriftError(
            STATUS_UNKNOWN_BUDGET,
            "CATEGORY_BYTES_BUDGET_EXCEEDED",
            f"Field {field_name!r} contains a string of {len(encoded)} UTF-8 bytes, exceeding limit {max_utf8_bytes}.",
        )


_ARRAY_DATA_ERRORS = (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowIndexError)


def _bounded_text_values(array: pa.Array, field_name: str, budget: Budget) -> list[str | None]:
    """Check offsets/byte lengths before UTF-8 scans or Python materialization."""
    try:
        array.validate(full=False)
        longest = pc.max(pc.binary_length(array)).as_py()
        if longest is not None and longest > budget.max_category_utf8_bytes:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "CATEGORY_BYTES_BUDGET_EXCEEDED")
        array.validate(full=True)
        return array.to_pylist()
    except (*_ARRAY_DATA_ERRORS, UnicodeDecodeError) as error:
        raise DriftError(STATUS_INVALID_INPUT, "INVALID_UTF8_STRING",
                         f"Field {field_name!r} contains invalid string data.") from error


def _scan_table_fields(
    table: pa.Table,
    specs: dict[str, _SourceFieldSpec],
    retain_values_for: set[str],
    ledger: WorkLedger,
    budget: Budget,
    selected_unions: dict[str, set[str]],
    dictionary_counts: dict[str, int] | None = None,
) -> dict[str, _ScannedFieldData]:
    """Validate every source cell using bounded slices and bounded dictionaries."""
    scanned: dict[str, _ScannedFieldData] = {}
    dictionary_counts = {} if dictionary_counts is None else dictionary_counts
    for name in sorted(specs, key=lambda value: value.encode("ascii")):
        spec = specs[name]
        column = table.column(name)
        keep = name in retain_values_for
        numeric_values = [] if keep and spec.logical_kind == "numeric" else None
        categorical_values = [] if keep and spec.logical_kind == "categorical" else None
        missing_count = 0
        dictionary_entries = dictionary_counts.get(name, 0)

        def admit_category(value: str) -> None:
            union = selected_unions.get(name)
            if union is not None and value not in union:
                if len(union) >= budget.max_category_union_per_field:
                    raise DriftError(STATUS_UNKNOWN_BUDGET, "CATEGORY_UNION_BUDGET_EXCEEDED")
                union.add(value)
            if categorical_values is not None:
                categorical_values.append(value)

        # Chunk headers were precharged from num_chunks before any traversal.
        # Do not use column.chunks: it materializes every Python chunk wrapper.
        for chunk_index in range(column.num_chunks):
            chunk = column.chunk(chunk_index)
            dictionary_values = None
            if pa.types.is_dictionary(chunk.type):
                dictionary = chunk.dictionary
                dictionary_entries += len(dictionary)
                if dictionary_entries > budget.max_dictionary_entries_per_field:
                    raise DriftError(STATUS_UNKNOWN_BUDGET, "DICTIONARY_ENTRY_BUDGET_EXCEEDED")
                dictionary_counts[name] = dictionary_entries
                ledger.charge_visits(len(dictionary))
                dictionary_values = _bounded_text_values(dictionary, name, budget)
            for offset in range(0, len(chunk), budget.max_batch_rows):
                batch = chunk.slice(offset, budget.max_batch_rows)
                ledger.charge_visits(len(batch))
                if dictionary_values is not None:
                    try:
                        batch.validate(full=True)
                        indices = batch.indices.to_pylist()
                    except _ARRAY_DATA_ERRORS as error:
                        raise DriftError(STATUS_INVALID_INPUT, "INVALID_DICTIONARY_INDICES") from error
                    for index in indices:
                        if index is None:
                            missing_count += 1
                        elif index < 0 or index >= len(dictionary_values):
                            raise DriftError(STATUS_INVALID_INPUT, "INVALID_DICTIONARY_INDICES")
                        elif dictionary_values[index] is None:
                            missing_count += 1
                        else:
                            admit_category(dictionary_values[index])
                elif spec.logical_kind == "categorical":
                    for value in _bounded_text_values(batch, name, budget):
                        if value is None:
                            missing_count += 1
                        else:
                            admit_category(value)
                else:
                    try:
                        batch.validate(full=True)
                        values = batch.to_pylist()
                    except _ARRAY_DATA_ERRORS as error:
                        raise DriftError(STATUS_INVALID_INPUT, "INVALID_NUMERIC_ARRAY") from error
                    for value in values:
                        if value is None:
                            missing_count += 1
                            continue
                        number = float(value)
                        if math.isnan(number):
                            missing_count += 1
                            continue
                        if math.isinf(number):
                            raise DriftError(STATUS_INVALID_INPUT, "NUMERIC_INFINITY")
                        if value < -NUMERIC_ABS_LIMIT or value > NUMERIC_ABS_LIMIT:
                            raise DriftError(STATUS_INVALID_INPUT, "NUMERIC_OUT_OF_RANGE")
                        if numeric_values is not None:
                            numeric_values.append(0.0 if number == 0.0 else number)
        scanned[name] = _ScannedFieldData(spec, table.num_rows, missing_count,
                                         table.num_rows - missing_count,
                                         numeric_values, categorical_values)
    return scanned


def validate_finite_metric_bounds(
    value: float,
    *,
    metric_name: str,
    lower_bound: float = 0.0,
    upper_bound: float | None = None,
) -> float:
    """Validate finite PSI/JSD bounds and snap only within 1e-14 of proven endpoints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DriftError(
            STATUS_NUMERICALLY_INDETERMINATE,
            f"NONFINITE_{metric_name.upper()}",
            f"{metric_name} result is not finite.",
        )
    fval = float(value)
    if fval < lower_bound - _ENDPOINT_SNAP_TOLERANCE:
        raise DriftError(
            STATUS_NUMERICALLY_INDETERMINATE,
            f"{metric_name.upper()}_BELOW_LOWER_BOUND",
            f"{metric_name} value {fval} is materially below {lower_bound}.",
        )
    if fval < lower_bound:
        fval = lower_bound
    if upper_bound is not None:
        if fval > upper_bound + _ENDPOINT_SNAP_TOLERANCE:
            raise DriftError(
                STATUS_NUMERICALLY_INDETERMINATE,
                f"{metric_name.upper()}_ABOVE_UPPER_BOUND",
                f"{metric_name} value {fval} is materially above {upper_bound}.",
            )
        if fval > upper_bound:
            fval = upper_bound
    if abs(fval) <= _ENDPOINT_SNAP_TOLERANCE and lower_bound == 0.0 and fval == 0.0:
        fval = 0.0
    return fval


def _compute_ks_counted_cdf_sweep(
    ref_sorted: np.ndarray,
    cur_sorted: np.ndarray,
    ledger: WorkLedger,
    budget: Budget,
) -> tuple[ExactFraction, int]:
    """Original deterministic counted-CDF merge sweep retaining exact integer rational KS."""
    n_r = int(ref_sorted.shape[0])
    n_c = int(cur_sorted.shape[0])
    i = 0
    j = 0
    max_cross_diff = 0
    union_points = 0

    while i < n_r or j < n_c:
        if i < n_r and (j == n_c or float(ref_sorted[i]) < float(cur_sorted[j])):
            t = float(ref_sorted[i])
        elif j < n_c and (i == n_r or float(cur_sorted[j]) < float(ref_sorted[i])):
            t = float(cur_sorted[j])
        else:
            t = float(ref_sorted[i])

        if union_points + 1 > budget.max_ks_union_points_per_field:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "KS_FIELD_UNION_POINTS_EXCEEDED",
                f"Selected numeric field exceeds {budget.max_ks_union_points_per_field} distinct KS union sweep points.",
            )
        ledger.charge_ks_step(1)
        union_points += 1

        while i < n_r and float(ref_sorted[i]) == t:
            i += 1
        while j < n_c and float(cur_sorted[j]) == t:
            j += 1

        cross_diff = abs(i * n_c - j * n_r)
        if cross_diff > max_cross_diff:
            max_cross_diff = cross_diff

    denom = n_r * n_c
    return ExactFraction(max_cross_diff, denom), union_points


def _compute_frozen_bin_counts(
    values: list[float],
    cutpoints_arr: np.ndarray,
    bin_count: int,
    ledger: WorkLedger,
) -> tuple[int, ...]:
    """Map nonmissing numeric observations into right-closed frozen bins."""
    n_vals = len(values)
    ledger.charge_visits(n_vals)
    if n_vals == 0:
        return tuple(0 for _ in range(bin_count))
    if cutpoints_arr.shape[0] == 0:
        return (n_vals,)
    vals_arr = np.asarray(values, dtype=np.float64)
    bin_indices = np.searchsorted(cutpoints_arr, vals_arr, side="left")
    counts = np.bincount(bin_indices, minlength=bin_count)
    return tuple(int(c) for c in counts.tolist())


def _compute_smoothed_psi(
    ref_bin_counts: tuple[int, ...],
    cur_bin_counts: tuple[int, ...],
    n_r: int,
    n_c: int,
) -> float:
    """Compute additive half-count smoothed PSI using maintained SciPy rel_entr primitives."""
    k_bins = len(ref_bin_counts)
    r_arr = np.asarray(ref_bin_counts, dtype=np.float64)
    c_arr = np.asarray(cur_bin_counts, dtype=np.float64)
    denom_r = float(n_r) + 0.5 * float(k_bins)
    denom_c = float(n_c) + 0.5 * float(k_bins)
    p = (r_arr + 0.5) / denom_r
    q = (c_arr + 0.5) / denom_c

    terms_pq = rel_entr(p, q)
    terms_qp = rel_entr(q, p)
    bin_terms = terms_pq + terms_qp
    for term in bin_terms.tolist():
        if not math.isfinite(float(term)):
            raise DriftError(
                STATUS_NUMERICALLY_INDETERMINATE,
                "NONFINITE_PSI_TERM",
                "PSI logarithmic term is non-finite.",
            )
    psi_raw = math.fsum(float(t) for t in bin_terms.tolist())
    psi_val = validate_finite_metric_bounds(psi_raw, metric_name="psi", lower_bound=0.0)
    if n_r == n_c and ref_bin_counts == cur_bin_counts and abs(psi_val) <= _ENDPOINT_SNAP_TOLERANCE:
        psi_val = 0.0
    return psi_val


def _compute_categorical_counts_and_jsd(
    ref_values: list[str],
    cur_values: list[str],
    ledger: WorkLedger,
    budget: Budget,
) -> tuple[int, int, int, int, int, int, float | None]:
    """Align observed UTF-8 categories, enforce union cap before growth, and compute JSD in bits."""
    union_keys: set[str] = set()
    ref_counts: dict[str, int] = {}
    cur_counts: dict[str, int] = {}

    for val in ref_values:
        ledger.charge_visits(1)
        if val not in union_keys:
            if len(union_keys) + 1 > budget.max_category_union_per_field:
                raise DriftError(
                    STATUS_UNKNOWN_BUDGET,
                    "CATEGORY_UNION_BUDGET_EXCEEDED",
                    f"Observed categorical union exceeds {budget.max_category_union_per_field} categories.",
                )
            union_keys.add(val)
        ref_counts[val] = ref_counts.get(val, 0) + 1

    for val in cur_values:
        ledger.charge_visits(1)
        if val not in union_keys:
            if len(union_keys) + 1 > budget.max_category_union_per_field:
                raise DriftError(
                    STATUS_UNKNOWN_BUDGET,
                    "CATEGORY_UNION_BUDGET_EXCEEDED",
                    f"Observed categorical union exceeds {budget.max_category_union_per_field} categories.",
                )
            union_keys.add(val)
        cur_counts[val] = cur_counts.get(val, 0) + 1

    ref_card = len(ref_counts)
    cur_card = len(cur_counts)
    union_card = len(union_keys)
    shared_card = len(ref_counts.keys() & cur_counts.keys())
    ref_only_card = ref_card - shared_card
    cur_only_card = cur_card - shared_card

    n_r = len(ref_values)
    n_c = len(cur_values)
    if n_r == 0 or n_c == 0:
        return (
            ref_card,
            cur_card,
            union_card,
            shared_card,
            ref_only_card,
            cur_only_card,
            None,
        )

    sorted_keys = sorted(union_keys, key=lambda s: s.encode("utf-8"))
    r_arr = np.asarray([ref_counts.get(k, 0) for k in sorted_keys], dtype=np.float64)
    c_arr = np.asarray([cur_counts.get(k, 0) for k in sorted_keys], dtype=np.float64)
    p = r_arr / float(n_r)
    q = c_arr / float(n_c)
    m = 0.5 * (p + q)

    kl_p_m = rel_entr(p, m)
    kl_q_m = rel_entr(q, m)
    terms_bits = 0.5 * (kl_p_m + kl_q_m) / _LN_2
    for term in terms_bits.tolist():
        if not math.isfinite(float(term)):
            raise DriftError(
                STATUS_NUMERICALLY_INDETERMINATE,
                "NONFINITE_JSD_TERM",
                "JSD logarithmic term is non-finite.",
            )
    jsd_raw = math.fsum(float(t) for t in terms_bits.tolist())
    jsd_val = validate_finite_metric_bounds(
        jsd_raw, metric_name="jsd", lower_bound=0.0, upper_bound=1.0
    )
    if shared_card == 0 and abs(jsd_val - 1.0) <= _ENDPOINT_SNAP_TOLERANCE:
        jsd_val = 1.0
    elif (
        all(ref_counts.get(k, 0) * n_c == cur_counts.get(k, 0) * n_r for k in sorted_keys)
        and abs(jsd_val) <= _ENDPOINT_SNAP_TOLERANCE
    ):
        jsd_val = 0.0

    return (
        ref_card,
        cur_card,
        union_card,
        shared_card,
        ref_only_card,
        cur_only_card,
        jsd_val,
    )


def _build_missingness_report(
    col_plan: ColumnPlan,
    ref_data: _ScannedFieldData | None,
    cur_data: _ScannedFieldData | None,
    ref_total_rows: int,
    cur_total_rows: int,
    schema_compatible: bool,
) -> MissingnessReport:
    ref_kind_matches = ref_data is not None
    cur_kind_matches = cur_data is not None

    ref_missing: int | None = ref_data.missing_count if ref_kind_matches and ref_data is not None else None
    ref_nonmissing: int | None = (
        ref_data.nonmissing_count if ref_kind_matches and ref_data is not None else None
    )
    cur_missing: int | None = cur_data.missing_count if cur_kind_matches and cur_data is not None else None
    cur_nonmissing: int | None = (
        cur_data.nonmissing_count if cur_kind_matches and cur_data is not None else None
    )

    ref_rate: ExactFraction | None = None
    if ref_missing is not None and ref_total_rows > 0:
        ref_rate = ExactFraction(ref_missing, ref_total_rows)

    cur_rate: ExactFraction | None = None
    if cur_missing is not None and cur_total_rows > 0:
        cur_rate = ExactFraction(cur_missing, cur_total_rows)

    if ref_data is None or cur_data is None:
        availability = AVAILABILITY_UNAVAILABLE_SCHEMA
        delta: ExactFraction | None = None
    elif ref_total_rows == 0 or cur_total_rows == 0:
        availability = AVAILABILITY_INSUFFICIENT_NONMISSING
        delta = None
    else:
        assert ref_rate is not None and cur_rate is not None
        availability = AVAILABILITY_AVAILABLE
        diff_frac = cur_rate.to_fraction() - ref_rate.to_fraction()
        delta = ExactFraction(diff_frac.numerator, diff_frac.denominator)

    return MissingnessReport(
        availability=availability,
        reference_total_rows=ref_total_rows,
        current_total_rows=cur_total_rows,
        reference_missing_count=ref_missing,
        current_missing_count=cur_missing,
        reference_nonmissing_count=ref_nonmissing,
        current_nonmissing_count=cur_nonmissing,
        reference_missing_rate=ref_rate,
        current_missing_rate=cur_rate,
        missing_rate_delta=delta,
    )


def compare_tables(
    reference: pa.Table,
    current: pa.Table,
    plan: ComparisonPlan,
    budget: Budget = DEFAULT_BUDGET,
) -> ComparisonReport:
    """Compare reference and current Arrow tables under an immutable ComparisonPlan and Budget."""
    if not isinstance(reference, pa.Table):
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_REFERENCE_TABLE",
            f"reference must be a pyarrow.Table, got {type(reference).__name__}.",
        )
    if not isinstance(current, pa.Table):
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_CURRENT_TABLE",
            f"current must be a pyarrow.Table, got {type(current).__name__}.",
        )
    if not isinstance(plan, ComparisonPlan):
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_COMPARISON_PLAN",
            f"plan must be a ComparisonPlan, got {type(plan).__name__}.",
        )
    if not isinstance(budget, Budget):
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_BUDGET",
            f"budget must be a Budget instance, got {type(budget).__name__}.",
        )

    for col_plan in plan.columns:
        if len(col_plan.name.encode("ascii")) > budget.max_field_name_bytes:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "FIELD_NAME_BYTES_EXCEEDED",
                f"Selected field {col_plan.name!r} exceeds budget field name byte limit {budget.max_field_name_bytes}.",
            )
        if len(col_plan.cutpoints) > budget.max_cutpoints_per_field:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "CUTPOINTS_PER_FIELD_EXCEEDED",
                f"Selected field {col_plan.name!r} has {len(col_plan.cutpoints)} cutpoints, exceeding budget {budget.max_cutpoints_per_field}.",
            )
    if len(plan.columns) > budget.max_selected_fields:
        raise DriftError(
            STATUS_UNKNOWN_BUDGET,
            "SELECTED_FIELDS_EXCEEDED",
            f"Plan selects {len(plan.columns)} fields, exceeding budget {budget.max_selected_fields}.",
        )

    ref_specs = _validate_table_schema(reference, "reference", budget)
    cur_specs = _validate_table_schema(current, "current", budget)

    selected_names = set(plan.selected_names)
    retained_bytes = sum(int(table.column(name).nbytes)
                         for table in (reference, current)
                         for name in selected_names if name in table.column_names)
    if retained_bytes > budget.max_retained_arrow_bytes:
        raise DriftError(
            STATUS_UNKNOWN_BUDGET,
            "RETAINED_ARROW_BYTES_EXCEEDED",
            f"Retained Arrow table bytes ({retained_bytes}) exceed limit {budget.max_retained_arrow_bytes}.",
        )

    schema_report = _build_schema_report(ref_specs, cur_specs)
    schema_by_name = {f.name: f for f in schema_report.fields}

    compatible_selected_names: set[str] = set()
    for col_plan in plan.columns:
        r_spec = ref_specs.get(col_plan.name)
        c_spec = cur_specs.get(col_plan.name)
        if (
            r_spec is not None
            and c_spec is not None
            and r_spec.logical_kind == col_plan.kind
            and c_spec.logical_kind == col_plan.kind
        ):
            compatible_selected_names.add(col_plan.name)

    ledger = WorkLedger(budget)

    prospective_chunk_headers = 0
    for name in sorted(ref_specs.keys(), key=lambda s: s.encode("ascii")):
        prospective_chunk_headers += int(reference.column(name).num_chunks)
    for name in sorted(cur_specs.keys(), key=lambda s: s.encode("ascii")):
        prospective_chunk_headers += int(current.column(name).num_chunks)
    ledger.charge_visits(prospective_chunk_headers)

    for table in (reference, current):
        present = [name for name in selected_names if name in table.column_names]
        for offset in range(0, table.num_rows, budget.max_batch_rows):
            size = sum(int(table.column(name).slice(offset, budget.max_batch_rows).nbytes)
                       for name in present)
            if size > budget.max_projected_batch_bytes:
                raise DriftError(STATUS_UNKNOWN_BUDGET, "PROJECTED_BATCH_BYTES_EXCEEDED")
    selected_unions = {name: set() for name in selected_names}

    ref_scanned = _scan_table_fields(
        reference, ref_specs, compatible_selected_names, ledger, budget, selected_unions
    )
    cur_scanned = _scan_table_fields(
        current, cur_specs, compatible_selected_names, ledger, budget, selected_unions
    )

    return _finish_comparison(ref_specs, cur_specs, ref_scanned, cur_scanned,
                              plan, budget, ledger, reference.num_rows, current.num_rows)


def _finish_comparison(ref_specs, cur_specs, ref_scanned, cur_scanned,
                       plan, budget, ledger, reference_rows, current_rows):
    """One statistics/report implementation, reused after private streaming validation."""
    schema_report = _build_schema_report(ref_specs, cur_specs)
    schema_by_name = {field.name: field for field in schema_report.fields}
    compatible_selected_names = {
        column.name for column in plan.columns
        if column.name in ref_specs and column.name in cur_specs
        and ref_specs[column.name].logical_kind == column.kind
        and cur_specs[column.name].logical_kind == column.kind
    }
    column_reports: list[ColumnReport] = []
    for col_plan in plan.columns:
        name = col_plan.name
        ref_data = ref_scanned.get(name)
        cur_data = cur_scanned.get(name)
        schema_entry = schema_by_name.get(name)
        if schema_entry is None:
            schema_status = "ABSENT"
            ref_phys = None
            cur_phys = None
        else:
            schema_status = schema_entry.status
            ref_phys = schema_entry.reference_physical_type
            cur_phys = schema_entry.current_physical_type
            if (
                schema_entry.present_in_reference
                and schema_entry.present_in_current
                and schema_entry.reference_logical_kind == schema_entry.current_logical_kind
                and schema_entry.reference_logical_kind != col_plan.kind
            ):
                schema_status = "PLAN_KIND_MISMATCH"

        schema_compatible = name in compatible_selected_names
        missingness = _build_missingness_report(
            col_plan,
            ref_data,
            cur_data,
            reference_rows,
            current_rows,
            schema_compatible,
        )

        if col_plan.kind == "numeric":
            bin_count = len(col_plan.cutpoints) + 1
            if not schema_compatible:
                col_availability = AVAILABILITY_UNAVAILABLE_SCHEMA
                num_report = NumericMetricReport(
                    availability=AVAILABILITY_UNAVAILABLE_SCHEMA,
                    reference_nonmissing_count=missingness.reference_nonmissing_count,
                    current_nonmissing_count=missingness.current_nonmissing_count,
                    cutpoints=col_plan.cutpoints,
                    bin_count=bin_count,
                    reference_bin_counts=None,
                    current_bin_counts=None,
                    smoothing_policy=_SMOOTHING_POLICY_NAME,
                    smoothing_alpha=_SMOOTHING_ALPHA,
                    ks_distance=None,
                    ks_statistic=None,
                    ks_sweep_points=None,
                    psi=None,
                )
            else:
                assert ref_data is not None and cur_data is not None
                assert ref_data.numeric_values is not None and cur_data.numeric_values is not None
                cutpoints_arr = np.asarray(col_plan.cutpoints, dtype=np.float64)
                ref_bins = _compute_frozen_bin_counts(
                    ref_data.numeric_values, cutpoints_arr, bin_count, ledger
                )
                cur_bins = _compute_frozen_bin_counts(
                    cur_data.numeric_values, cutpoints_arr, bin_count, ledger
                )
                n_r = ref_data.nonmissing_count
                n_c = cur_data.nonmissing_count
                if n_r == 0 or n_c == 0:
                    col_availability = AVAILABILITY_INSUFFICIENT_NONMISSING
                    num_report = NumericMetricReport(
                        availability=AVAILABILITY_INSUFFICIENT_NONMISSING,
                        reference_nonmissing_count=n_r,
                        current_nonmissing_count=n_c,
                        cutpoints=col_plan.cutpoints,
                        bin_count=bin_count,
                        reference_bin_counts=ref_bins,
                        current_bin_counts=cur_bins,
                        smoothing_policy=_SMOOTHING_POLICY_NAME,
                        smoothing_alpha=_SMOOTHING_ALPHA,
                        ks_distance=None,
                        ks_statistic=None,
                        ks_sweep_points=None,
                        psi=None,
                    )
                else:
                    col_availability = AVAILABILITY_AVAILABLE
                    ref_sorted = np.sort(
                        np.asarray(ref_data.numeric_values, dtype=np.float64),
                        kind="mergesort",
                    )
                    cur_sorted = np.sort(
                        np.asarray(cur_data.numeric_values, dtype=np.float64),
                        kind="mergesort",
                    )
                    ks_frac, ks_points = _compute_ks_counted_cdf_sweep(
                        ref_sorted, cur_sorted, ledger, budget
                    )
                    psi_val = _compute_smoothed_psi(ref_bins, cur_bins, n_r, n_c)
                    num_report = NumericMetricReport(
                        availability=AVAILABILITY_AVAILABLE,
                        reference_nonmissing_count=n_r,
                        current_nonmissing_count=n_c,
                        cutpoints=col_plan.cutpoints,
                        bin_count=bin_count,
                        reference_bin_counts=ref_bins,
                        current_bin_counts=cur_bins,
                        smoothing_policy=_SMOOTHING_POLICY_NAME,
                        smoothing_alpha=_SMOOTHING_ALPHA,
                        ks_distance=ks_frac,
                        ks_statistic=ks_frac.value,
                        ks_sweep_points=ks_points,
                        psi=psi_val,
                    )
            column_reports.append(
                ColumnReport(
                    name=name,
                    kind="numeric",
                    availability=col_availability,
                    schema_status=schema_status,
                    reference_physical_type=ref_phys,
                    current_physical_type=cur_phys,
                    missingness=missingness,
                    numeric=num_report,
                    categorical=None,
                )
            )
        else:
            if not schema_compatible:
                col_availability = AVAILABILITY_UNAVAILABLE_SCHEMA
                cat_report = CategoricalMetricReport(
                    availability=AVAILABILITY_UNAVAILABLE_SCHEMA,
                    reference_nonmissing_count=missingness.reference_nonmissing_count,
                    current_nonmissing_count=missingness.current_nonmissing_count,
                    reference_cardinality=None,
                    current_cardinality=None,
                    union_cardinality=None,
                    shared_cardinality=None,
                    reference_only_cardinality=None,
                    current_only_cardinality=None,
                    js_divergence_bits=None,
                )
            else:
                assert ref_data is not None and cur_data is not None
                assert (
                    ref_data.categorical_values is not None
                    and cur_data.categorical_values is not None
                )
                (
                    ref_card,
                    cur_card,
                    union_card,
                    shared_card,
                    ref_only_card,
                    cur_only_card,
                    jsd_val,
                ) = _compute_categorical_counts_and_jsd(
                    ref_data.categorical_values,
                    cur_data.categorical_values,
                    ledger,
                    budget,
                )
                n_r = ref_data.nonmissing_count
                n_c = cur_data.nonmissing_count
                if n_r == 0 or n_c == 0:
                    col_availability = AVAILABILITY_INSUFFICIENT_NONMISSING
                else:
                    col_availability = AVAILABILITY_AVAILABLE
                cat_report = CategoricalMetricReport(
                    availability=col_availability,
                    reference_nonmissing_count=n_r,
                    current_nonmissing_count=n_c,
                    reference_cardinality=ref_card,
                    current_cardinality=cur_card,
                    union_cardinality=union_card,
                    shared_cardinality=shared_card,
                    reference_only_cardinality=ref_only_card,
                    current_only_cardinality=cur_only_card,
                    js_divergence_bits=jsd_val,
                )
            column_reports.append(
                ColumnReport(
                    name=name,
                    kind="categorical",
                    availability=col_availability,
                    schema_status=schema_status,
                    reference_physical_type=ref_phys,
                    current_physical_type=cur_phys,
                    missingness=missingness,
                    numeric=None,
                    categorical=cat_report,
                )
            )

    work_report = LogicalWorkReport(
        value_visits=ledger.value_visits,
        ks_sweep_steps=ledger.ks_sweep_steps,
        max_value_visits=budget.max_value_visits,
        max_ks_sweep_steps=budget.max_ks_sweep_steps,
        max_report_bytes=budget.max_report_bytes,
    )
    return ComparisonReport(
        plan=plan,
        reference_row_count=reference_rows,
        current_row_count=current_rows,
        schema=schema_report,
        columns=column_reports,
        work=work_report,
        max_report_bytes=budget.max_report_bytes,
    )
