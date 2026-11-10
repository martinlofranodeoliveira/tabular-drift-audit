"""Immutable plan, budget, and report types for Tabular Drift Audit.

Copyright (c) 2026 Lofrano Tecnologia (https://lofranotecnologia.com.br)
GitHub owner: martinlofranodeoliveira
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import islice
import json
import math
from typing import Any

from tabular_drift_audit.errors import (
    AVAILABILITY_AVAILABLE,
    STATUS_COMPLETE,
    STATUS_INVALID_INPUT,
    STATUS_UNKNOWN_BUDGET,
    VALID_AVAILABILITY_STATES,
    DriftError,
)

HARD_MAX_ROWS_PER_DATASET = 16_384
HARD_MAX_SOURCE_FIELDS_PER_DATASET = 32
HARD_MAX_SELECTED_FIELDS = 16
HARD_MAX_FIELD_NAME_BYTES = 64
HARD_MAX_CUTPOINTS_PER_FIELD = 15
HARD_MAX_NUMERIC_BINS_PER_FIELD = 16
HARD_MAX_CATEGORY_UTF8_BYTES = 256
HARD_MAX_CATEGORY_UNION_PER_FIELD = 128
HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD = 16_384
HARD_MAX_RETAINED_ARROW_BYTES = 67_108_864
HARD_MAX_VALUE_VISITS = 2_097_152
HARD_MAX_KS_SWEEP_STEPS = 524_288
HARD_MAX_KS_UNION_POINTS_PER_FIELD = 32_768
HARD_MAX_REPORT_BYTES = 8_388_608
HARD_MAX_BATCH_ROWS = 256
HARD_MAX_PROJECTED_BATCH_BYTES = 4_194_304
NUMERIC_ABS_LIMIT = 1_000_000_000_000.0

VALID_COLUMN_KINDS = frozenset({"numeric", "categorical"})


def validate_ascii_field_name(name: Any, max_bytes: int = HARD_MAX_FIELD_NAME_BYTES) -> str:
    """Validate a non-empty ASCII field name within its byte budget."""
    if not isinstance(name, str) or len(name) == 0:
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_FIELD_NAME",
            "Field name must be a non-empty ASCII string.",
        )
    if not name.isascii():
        raise DriftError(
            STATUS_INVALID_INPUT,
            "NON_ASCII_FIELD_NAME",
            "Field name must contain only ASCII characters.",
        )
    encoded_len = len(name.encode("ascii"))
    if encoded_len > HARD_MAX_FIELD_NAME_BYTES or encoded_len > max_bytes:
        raise DriftError(
            STATUS_UNKNOWN_BUDGET,
            "FIELD_NAME_BYTES_EXCEEDED",
            f"Field name ASCII byte length {encoded_len} exceeds limit {min(HARD_MAX_FIELD_NAME_BYTES, max_bytes)}.",
        )
    return name


def _validate_strict_positive_int(value: Any, field_name: str, hard_cap: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DriftError(
            STATUS_INVALID_INPUT,
            "INVALID_BUDGET_TYPE",
            f"Budget parameter {field_name!r} must be a strict int, not {type(value).__name__}.",
        )
    if value <= 0:
        raise DriftError(
            STATUS_INVALID_INPUT,
            "NONPOSITIVE_BUDGET_VALUE",
            f"Budget parameter {field_name!r} must be strictly positive.",
        )
    if value > hard_cap:
        raise DriftError(
            STATUS_INVALID_INPUT,
            "BUDGET_EXCEEDS_HARD_CAP",
            f"Budget parameter {field_name!r}={value} cannot exceed hard cap {hard_cap}.",
        )
    return value


@dataclass(frozen=True, slots=True)
class ExactFraction:
    """Immutable reduced rational representation with exact numerator/denominator and float value."""

    numerator: int
    denominator: int
    value: float

    def __init__(self, numerator: int, denominator: int = 1) -> None:
        if isinstance(numerator, bool) or not isinstance(numerator, int):
            raise DriftError(
                STATUS_INVALID_INPUT,
                "INVALID_FRACTION_NUMERATOR",
                "ExactFraction numerator must be a strict integer.",
            )
        if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator <= 0:
            raise DriftError(
                STATUS_INVALID_INPUT,
                "INVALID_FRACTION_DENOMINATOR",
                "ExactFraction denominator must be a positive strict integer.",
            )
        frac = Fraction(numerator, denominator)
        fval = float(frac.numerator) / float(frac.denominator)
        if fval == 0.0:
            fval = 0.0
        object.__setattr__(self, "numerator", int(frac.numerator))
        object.__setattr__(self, "denominator", int(frac.denominator))
        object.__setattr__(self, "value", fval)

    def to_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ColumnPlan:
    """Immutable specification for a single selected column in a ComparisonPlan."""

    name: str
    kind: str
    cutpoints: tuple[float, ...]

    def __init__(
        self,
        name: str,
        kind: str,
        cutpoints: Iterable[float | int] | None = None,
    ) -> None:
        validated_name = validate_ascii_field_name(name, HARD_MAX_FIELD_NAME_BYTES)
        if not isinstance(kind, str) or kind not in VALID_COLUMN_KINDS:
            raise DriftError(
                STATUS_INVALID_INPUT,
                "INVALID_COLUMN_KIND",
                f"Column kind must be 'numeric' or 'categorical', got {kind!r}.",
            )
        if kind == "categorical":
            if cutpoints is not None:
                if isinstance(cutpoints, (str, bytes, bytearray, Mapping)) or not isinstance(
                    cutpoints, Iterable
                ):
                    raise DriftError(
                        STATUS_INVALID_INPUT,
                        "INVALID_CUTPOINTS_CONTAINER",
                        "Categorical column cannot accept non-empty or malformed cutpoints.",
                    )
                materialized_cat = tuple(islice(cutpoints, 1))
                if len(materialized_cat) > 0:
                    raise DriftError(
                        STATUS_INVALID_INPUT,
                        "CATEGORICAL_CUTPOINTS_FORBIDDEN",
                        "Categorical column plan must not define numeric cutpoints.",
                    )
            frozen_cutpoints: tuple[float, ...] = ()
        else:
            if cutpoints is None:
                frozen_cutpoints = ()
            else:
                if isinstance(cutpoints, (str, bytes, bytearray, Mapping)) or not isinstance(
                    cutpoints, Iterable
                ):
                    raise DriftError(
                        STATUS_INVALID_INPUT,
                        "INVALID_CUTPOINTS_CONTAINER",
                        "Cutpoints must be an iterable of finite numbers.",
                    )
                raw_items = list(islice(cutpoints, HARD_MAX_CUTPOINTS_PER_FIELD + 1))
                if len(raw_items) > HARD_MAX_CUTPOINTS_PER_FIELD:
                    raise DriftError(STATUS_UNKNOWN_BUDGET, "CUTPOINTS_PER_FIELD_EXCEEDED")
                normalized: list[float] = []
                for item in raw_items:
                    if isinstance(item, bool) or not isinstance(item, (int, float)):
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "INVALID_CUTPOINT_TYPE",
                            "Each cutpoint must be a finite int or float (excluding bool).",
                        )
                    try:
                        fval = float(item)
                    except (OverflowError, ValueError) as exc:
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "NONFINITE_CUTPOINT",
                            "Cutpoint could not be converted to finite float64.",
                        ) from exc
                    if not math.isfinite(fval):
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "NONFINITE_CUTPOINT",
                            "Cutpoints must be finite numbers.",
                        )
                    if isinstance(item, int) and abs(item) > int(NUMERIC_ABS_LIMIT):
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "CUTPOINT_OUT_OF_RANGE",
                            "Cutpoint exceeds [-1e12, 1e12] domain.",
                        )
                    if fval < -NUMERIC_ABS_LIMIT or fval > NUMERIC_ABS_LIMIT:
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "CUTPOINT_OUT_OF_RANGE",
                            "Cutpoints must lie within [-1e12, 1e12].",
                        )
                    if fval == 0.0:
                        fval = 0.0
                    normalized.append(fval)
                for idx in range(len(normalized) - 1):
                    if not (normalized[idx] < normalized[idx + 1]):
                        raise DriftError(
                            STATUS_INVALID_INPUT,
                            "UNSORTED_OR_DUPLICATE_CUTPOINTS",
                            "Cutpoints must be strictly increasing.",
                        )
                if len(normalized) > HARD_MAX_CUTPOINTS_PER_FIELD:
                    raise DriftError(
                        STATUS_UNKNOWN_BUDGET,
                        "CUTPOINTS_PER_FIELD_EXCEEDED",
                        f"Numeric column defines {len(normalized)} cutpoints, exceeding limit {HARD_MAX_CUTPOINTS_PER_FIELD}.",
                    )
                frozen_cutpoints = tuple(normalized)

        object.__setattr__(self, "name", validated_name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "cutpoints", frozen_cutpoints)

    @property
    def bin_count(self) -> int | None:
        if self.kind == "numeric":
            return len(self.cutpoints) + 1
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "cutpoints": list(self.cutpoints),
        }


@dataclass(frozen=True, slots=True)
class ComparisonPlan:
    """Immutable comparison plan with canonically ordered ColumnPlan entries."""

    columns: tuple[ColumnPlan, ...]

    def __init__(self, columns: Iterable[ColumnPlan]) -> None:
        if isinstance(columns, (str, bytes, bytearray, Mapping)) or not isinstance(
            columns, Iterable
        ):
            raise DriftError(
                STATUS_INVALID_INPUT,
                "INVALID_PLAN_COLUMNS_CONTAINER",
                "ComparisonPlan columns must be a sequence or iterable of ColumnPlan objects.",
            )
        raw_columns = list(islice(columns, HARD_MAX_SELECTED_FIELDS + 1))
        if len(raw_columns) > HARD_MAX_SELECTED_FIELDS:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "SELECTED_FIELDS_EXCEEDED")
        if len(raw_columns) == 0:
            raise DriftError(
                STATUS_INVALID_INPUT,
                "EMPTY_COMPARISON_PLAN",
                "ComparisonPlan must select at least 1 column.",
            )
        seen_names: set[str] = set()
        for col in raw_columns:
            if not isinstance(col, ColumnPlan):
                raise DriftError(
                    STATUS_INVALID_INPUT,
                    "INVALID_COLUMN_PLAN_ITEM",
                    "Every item in ComparisonPlan must be a ColumnPlan instance.",
                )
            if col.name in seen_names:
                raise DriftError(
                    STATUS_INVALID_INPUT,
                    "DUPLICATE_PLAN_FIELD_NAME",
                    f"Duplicate column name {col.name!r} in ComparisonPlan.",
                )
            seen_names.add(col.name)

        if len(raw_columns) > HARD_MAX_SELECTED_FIELDS:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "SELECTED_FIELDS_EXCEEDED",
                f"ComparisonPlan selects {len(raw_columns)} fields, exceeding limit {HARD_MAX_SELECTED_FIELDS}.",
            )
        canonical = tuple(sorted(raw_columns, key=lambda c: c.name.encode("ascii")))
        object.__setattr__(self, "columns", canonical)

    @property
    def fields(self) -> tuple[ColumnPlan, ...]:
        return self.columns

    @property
    def selected_names(self) -> tuple[str, ...]:
        return tuple(col.name for col in self.columns)

    def __len__(self) -> int:
        return len(self.columns)

    def __iter__(self):
        return iter(self.columns)

    def __getitem__(self, index: int) -> ColumnPlan:
        return self.columns[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": [col.to_dict() for col in self.columns],
        }


@dataclass(frozen=True, slots=True)
class Budget:
    """Immutable resource and logical work budget caps for a comparison operation."""

    max_value_visits: int
    max_ks_sweep_steps: int
    max_report_bytes: int
    max_rows_per_dataset: int
    max_source_fields_per_dataset: int
    max_selected_fields: int
    max_field_name_bytes: int
    max_cutpoints_per_field: int
    max_category_utf8_bytes: int
    max_category_union_per_field: int
    max_dictionary_entries_per_field: int
    max_retained_arrow_bytes: int
    max_ks_union_points_per_field: int
    max_batch_rows: int
    max_projected_batch_bytes: int

    def __init__(
        self,
        *,
        max_value_visits: int = HARD_MAX_VALUE_VISITS,
        max_ks_sweep_steps: int = HARD_MAX_KS_SWEEP_STEPS,
        max_report_bytes: int = HARD_MAX_REPORT_BYTES,
        max_rows_per_dataset: int = HARD_MAX_ROWS_PER_DATASET,
        max_source_fields_per_dataset: int = HARD_MAX_SOURCE_FIELDS_PER_DATASET,
        max_selected_fields: int = HARD_MAX_SELECTED_FIELDS,
        max_field_name_bytes: int = HARD_MAX_FIELD_NAME_BYTES,
        max_cutpoints_per_field: int = HARD_MAX_CUTPOINTS_PER_FIELD,
        max_category_utf8_bytes: int = HARD_MAX_CATEGORY_UTF8_BYTES,
        max_category_union_per_field: int = HARD_MAX_CATEGORY_UNION_PER_FIELD,
        max_dictionary_entries_per_field: int = HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD,
        max_retained_arrow_bytes: int = HARD_MAX_RETAINED_ARROW_BYTES,
        max_ks_union_points_per_field: int = HARD_MAX_KS_UNION_POINTS_PER_FIELD,
        max_batch_rows: int = HARD_MAX_BATCH_ROWS,
        max_projected_batch_bytes: int = HARD_MAX_PROJECTED_BATCH_BYTES,
        **aliases: Any,
    ) -> None:
        alias_map = {
            "original_value_visits": ("max_value_visits", HARD_MAX_VALUE_VISITS),
            "original_KS_union_sweep_steps": ("max_ks_sweep_steps", HARD_MAX_KS_SWEEP_STEPS),
            "serialized_report_bytes": ("max_report_bytes", HARD_MAX_REPORT_BYTES),
            "rows_per_dataset": ("max_rows_per_dataset", HARD_MAX_ROWS_PER_DATASET),
            "source_fields_per_dataset": (
                "max_source_fields_per_dataset",
                HARD_MAX_SOURCE_FIELDS_PER_DATASET,
            ),
            "selected_fields": ("max_selected_fields", HARD_MAX_SELECTED_FIELDS),
            "field_name_ASCII_bytes": ("max_field_name_bytes", HARD_MAX_FIELD_NAME_BYTES),
            "numeric_cutpoints_per_field": (
                "max_cutpoints_per_field",
                HARD_MAX_CUTPOINTS_PER_FIELD,
            ),
            "category_UTF8_bytes": ("max_category_utf8_bytes", HARD_MAX_CATEGORY_UTF8_BYTES),
            "category_union_per_selected_field": (
                "max_category_union_per_field",
                HARD_MAX_CATEGORY_UNION_PER_FIELD,
            ),
            "Arrow_dictionary_entries_per_source_field": (
                "max_dictionary_entries_per_field",
                HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD,
            ),
            "retained_projected_Arrow_bytes_across_both_inputs": (
                "max_retained_arrow_bytes",
                HARD_MAX_RETAINED_ARROW_BYTES,
            ),
        }
        values: dict[str, int] = {
            "max_batch_rows": _validate_strict_positive_int(max_batch_rows, "max_batch_rows", HARD_MAX_BATCH_ROWS),
            "max_projected_batch_bytes": _validate_strict_positive_int(max_projected_batch_bytes, "max_projected_batch_bytes", HARD_MAX_PROJECTED_BATCH_BYTES),
            "max_value_visits": _validate_strict_positive_int(
                max_value_visits, "max_value_visits", HARD_MAX_VALUE_VISITS
            ),
            "max_ks_sweep_steps": _validate_strict_positive_int(
                max_ks_sweep_steps, "max_ks_sweep_steps", HARD_MAX_KS_SWEEP_STEPS
            ),
            "max_report_bytes": _validate_strict_positive_int(
                max_report_bytes, "max_report_bytes", HARD_MAX_REPORT_BYTES
            ),
            "max_rows_per_dataset": _validate_strict_positive_int(
                max_rows_per_dataset, "max_rows_per_dataset", HARD_MAX_ROWS_PER_DATASET
            ),
            "max_source_fields_per_dataset": _validate_strict_positive_int(
                max_source_fields_per_dataset,
                "max_source_fields_per_dataset",
                HARD_MAX_SOURCE_FIELDS_PER_DATASET,
            ),
            "max_selected_fields": _validate_strict_positive_int(
                max_selected_fields, "max_selected_fields", HARD_MAX_SELECTED_FIELDS
            ),
            "max_field_name_bytes": _validate_strict_positive_int(
                max_field_name_bytes, "max_field_name_bytes", HARD_MAX_FIELD_NAME_BYTES
            ),
            "max_cutpoints_per_field": _validate_strict_positive_int(
                max_cutpoints_per_field,
                "max_cutpoints_per_field",
                HARD_MAX_CUTPOINTS_PER_FIELD,
            ),
            "max_category_utf8_bytes": _validate_strict_positive_int(
                max_category_utf8_bytes,
                "max_category_utf8_bytes",
                HARD_MAX_CATEGORY_UTF8_BYTES,
            ),
            "max_category_union_per_field": _validate_strict_positive_int(
                max_category_union_per_field,
                "max_category_union_per_field",
                HARD_MAX_CATEGORY_UNION_PER_FIELD,
            ),
            "max_dictionary_entries_per_field": _validate_strict_positive_int(
                max_dictionary_entries_per_field,
                "max_dictionary_entries_per_field",
                HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD,
            ),
            "max_retained_arrow_bytes": _validate_strict_positive_int(
                max_retained_arrow_bytes,
                "max_retained_arrow_bytes",
                HARD_MAX_RETAINED_ARROW_BYTES,
            ),
            "max_ks_union_points_per_field": _validate_strict_positive_int(
                max_ks_union_points_per_field,
                "max_ks_union_points_per_field",
                HARD_MAX_KS_UNION_POINTS_PER_FIELD,
            ),
        }
        for alias_key, alias_val in aliases.items():
            if alias_key not in alias_map:
                raise DriftError(
                    STATUS_INVALID_INPUT,
                    "UNKNOWN_BUDGET_PARAMETER",
                    f"Unknown Budget parameter {alias_key!r}.",
                )
            target_attr, hard_cap = alias_map[alias_key]
            validated = _validate_strict_positive_int(alias_val, alias_key, hard_cap)
            if values[target_attr] != hard_cap and values[target_attr] != validated:
                raise DriftError(STATUS_INVALID_INPUT, "CONFLICTING_BUDGET_PARAMETERS")
            values[target_attr] = validated

        for k, v in values.items():
            object.__setattr__(self, k, v)

    @property
    def original_value_visits(self) -> int:
        return self.max_value_visits

    @property
    def original_KS_union_sweep_steps(self) -> int:
        return self.max_ks_sweep_steps

    @property
    def serialized_report_bytes(self) -> int:
        return self.max_report_bytes

    def to_dict(self) -> dict[str, int]:
        return {
            "max_batch_rows": self.max_batch_rows,
            "max_projected_batch_bytes": self.max_projected_batch_bytes,
            "max_value_visits": self.max_value_visits,
            "max_ks_sweep_steps": self.max_ks_sweep_steps,
            "max_report_bytes": self.max_report_bytes,
            "max_rows_per_dataset": self.max_rows_per_dataset,
            "max_source_fields_per_dataset": self.max_source_fields_per_dataset,
            "max_selected_fields": self.max_selected_fields,
            "max_field_name_bytes": self.max_field_name_bytes,
            "max_cutpoints_per_field": self.max_cutpoints_per_field,
            "max_category_utf8_bytes": self.max_category_utf8_bytes,
            "max_category_union_per_field": self.max_category_union_per_field,
            "max_dictionary_entries_per_field": self.max_dictionary_entries_per_field,
            "max_retained_arrow_bytes": self.max_retained_arrow_bytes,
            "max_ks_union_points_per_field": self.max_ks_union_points_per_field,
        }


DEFAULT_BUDGET = Budget()


@dataclass(frozen=True, slots=True)
class FieldSchemaComparison:
    """Immutable per-field schema comparison entry."""

    name: str
    present_in_reference: bool
    present_in_current: bool
    reference_physical_type: str | None
    current_physical_type: str | None
    reference_logical_kind: str | None
    current_logical_kind: str | None
    physical_type_changed: bool
    logical_type_changed: bool
    compatible_for_metrics: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "present_in_reference": self.present_in_reference,
            "present_in_current": self.present_in_current,
            "reference_physical_type": self.reference_physical_type,
            "current_physical_type": self.current_physical_type,
            "reference_logical_kind": self.reference_logical_kind,
            "current_logical_kind": self.current_logical_kind,
            "physical_type_changed": self.physical_type_changed,
            "logical_type_changed": self.logical_type_changed,
            "compatible_for_metrics": self.compatible_for_metrics,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class SchemaReport:
    """Immutable source schema comparison report ordered by ASCII field name."""

    reference_field_names: tuple[str, ...]
    current_field_names: tuple[str, ...]
    common_fields: tuple[str, ...]
    added_fields: tuple[str, ...]
    dropped_fields: tuple[str, ...]
    compatible_physical_changes: tuple[str, ...]
    incompatible_type_changes: tuple[str, ...]
    fields: tuple[FieldSchemaComparison, ...]

    def field(self, name: str) -> FieldSchemaComparison:
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_field_names": list(self.reference_field_names),
            "current_field_names": list(self.current_field_names),
            "common_fields": list(self.common_fields),
            "added_fields": list(self.added_fields),
            "dropped_fields": list(self.dropped_fields),
            "compatible_physical_changes": list(self.compatible_physical_changes),
            "incompatible_type_changes": list(self.incompatible_type_changes),
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True, slots=True)
class MissingnessReport:
    """Immutable missingness report for a selected column."""

    availability: str
    reference_total_rows: int
    current_total_rows: int
    reference_missing_count: int | None
    current_missing_count: int | None
    reference_nonmissing_count: int | None
    current_nonmissing_count: int | None
    reference_missing_rate: ExactFraction | None
    current_missing_rate: ExactFraction | None
    missing_rate_delta: ExactFraction | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "reference_total_rows": self.reference_total_rows,
            "current_total_rows": self.current_total_rows,
            "reference_missing_count": self.reference_missing_count,
            "current_missing_count": self.current_missing_count,
            "reference_nonmissing_count": self.reference_nonmissing_count,
            "current_nonmissing_count": self.current_nonmissing_count,
            "reference_missing_rate": (
                self.reference_missing_rate.to_dict()
                if self.reference_missing_rate is not None
                else None
            ),
            "current_missing_rate": (
                self.current_missing_rate.to_dict()
                if self.current_missing_rate is not None
                else None
            ),
            "missing_rate_delta": (
                self.missing_rate_delta.to_dict()
                if self.missing_rate_delta is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class NumericMetricReport:
    """Immutable KS and smoothed PSI report for a selected numeric column."""

    availability: str
    reference_nonmissing_count: int | None
    current_nonmissing_count: int | None
    cutpoints: tuple[float, ...]
    bin_count: int
    reference_bin_counts: tuple[int, ...] | None
    current_bin_counts: tuple[int, ...] | None
    smoothing_policy: str
    smoothing_alpha: ExactFraction
    ks_distance: ExactFraction | None
    ks_statistic: float | None
    ks_sweep_points: int | None
    psi: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "reference_nonmissing_count": self.reference_nonmissing_count,
            "current_nonmissing_count": self.current_nonmissing_count,
            "cutpoints": list(self.cutpoints),
            "bin_count": self.bin_count,
            "reference_bin_counts": (
                list(self.reference_bin_counts)
                if self.reference_bin_counts is not None
                else None
            ),
            "current_bin_counts": (
                list(self.current_bin_counts)
                if self.current_bin_counts is not None
                else None
            ),
            "smoothing_policy": self.smoothing_policy,
            "smoothing_alpha": self.smoothing_alpha.to_dict(),
            "ks_distance": (
                self.ks_distance.to_dict() if self.ks_distance is not None else None
            ),
            "ks_statistic": self.ks_statistic,
            "ks_sweep_points": self.ks_sweep_points,
            "psi": self.psi,
        }


@dataclass(frozen=True, slots=True)
class CategoricalMetricReport:
    """Immutable categorical cardinality and Jensen-Shannon divergence report (without category labels)."""

    availability: str
    reference_nonmissing_count: int | None
    current_nonmissing_count: int | None
    reference_cardinality: int | None
    current_cardinality: int | None
    union_cardinality: int | None
    shared_cardinality: int | None
    reference_only_cardinality: int | None
    current_only_cardinality: int | None
    js_divergence_bits: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "reference_nonmissing_count": self.reference_nonmissing_count,
            "current_nonmissing_count": self.current_nonmissing_count,
            "reference_cardinality": self.reference_cardinality,
            "current_cardinality": self.current_cardinality,
            "union_cardinality": self.union_cardinality,
            "shared_cardinality": self.shared_cardinality,
            "reference_only_cardinality": self.reference_only_cardinality,
            "current_only_cardinality": self.current_only_cardinality,
            "js_divergence_bits": self.js_divergence_bits,
        }


@dataclass(frozen=True, slots=True)
class ColumnReport:
    """Immutable report for a single selected column in the ComparisonPlan."""

    name: str
    kind: str
    availability: str
    schema_status: str
    reference_physical_type: str | None
    current_physical_type: str | None
    missingness: MissingnessReport
    numeric: NumericMetricReport | None
    categorical: CategoricalMetricReport | None

    def __post_init__(self) -> None:
        if self.availability not in VALID_AVAILABILITY_STATES:
            raise ValueError(f"Invalid availability state: {self.availability!r}")

    @property
    def available(self) -> bool:
        return self.availability == AVAILABILITY_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "availability": self.availability,
            "schema_status": self.schema_status,
            "reference_physical_type": self.reference_physical_type,
            "current_physical_type": self.current_physical_type,
            "missingness": self.missingness.to_dict(),
            "numeric": self.numeric.to_dict() if self.numeric is not None else None,
            "categorical": (
                self.categorical.to_dict() if self.categorical is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class LogicalWorkReport:
    """Immutable accounting of operation-wide logical visits and KS sweep steps."""

    value_visits: int
    ks_sweep_steps: int
    max_value_visits: int
    max_ks_sweep_steps: int
    max_report_bytes: int

    def to_dict(self) -> dict[str, int]:
        return {
            "value_visits": self.value_visits,
            "ks_sweep_steps": self.ks_sweep_steps,
            "max_value_visits": self.max_value_visits,
            "max_ks_sweep_steps": self.max_ks_sweep_steps,
            "max_report_bytes": self.max_report_bytes,
        }


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Immutable complete comparison report with pre-validated deterministic JSON serialization."""

    status: str
    complete: bool
    plan: ComparisonPlan
    reference_row_count: int
    current_row_count: int
    schema: SchemaReport
    columns: tuple[ColumnReport, ...]
    work: LogicalWorkReport
    _json_bytes: bytes

    def __init__(
        self,
        *,
        plan: ComparisonPlan,
        reference_row_count: int,
        current_row_count: int,
        schema: SchemaReport,
        columns: Iterable[ColumnReport],
        work: LogicalWorkReport,
        max_report_bytes: int = HARD_MAX_REPORT_BYTES,
    ) -> None:
        cols_tuple = tuple(columns)
        object.__setattr__(self, "status", STATUS_COMPLETE)
        object.__setattr__(self, "complete", True)
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "reference_row_count", int(reference_row_count))
        object.__setattr__(self, "current_row_count", int(current_row_count))
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "columns", cols_tuple)
        object.__setattr__(self, "work", work)

        payload = {
            "status": STATUS_COMPLETE,
            "complete": True,
            "plan": plan.to_dict(),
            "reference_row_count": int(reference_row_count),
            "current_row_count": int(current_row_count),
            "schema": schema.to_dict(),
            "columns": [c.to_dict() for c in cols_tuple],
            "work": work.to_dict(),
        }
        serialized_str = json.dumps(
            payload,
            allow_nan=False,
            sort_keys=False,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        serialized_bytes = serialized_str.encode("utf-8")
        if len(serialized_bytes) > max_report_bytes:
            raise DriftError(
                STATUS_UNKNOWN_BUDGET,
                "REPORT_BYTES_BUDGET_EXCEEDED",
                f"Serialized report size {len(serialized_bytes)} bytes exceeds limit {max_report_bytes}.",
            )
        object.__setattr__(self, "_json_bytes", serialized_bytes)

    def column(self, name: str) -> ColumnReport:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    @property
    def serialized_report_bytes(self) -> int:
        return len(self._json_bytes)

    def to_json_bytes(self) -> bytes:
        return self._json_bytes

    def to_json(self) -> str:
        return self._json_bytes.decode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json_bytes.decode("utf-8"))
