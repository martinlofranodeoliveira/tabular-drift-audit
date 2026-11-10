"""Bounded typed Arrow comparison SDK.

Copyright (c) 2026 Lofrano Tecnologia (https://lofranotecnologia.com.br)
GitHub owner: martinlofranodeoliveira
SPDX-License-Identifier: MIT
"""

from tabular_drift_audit.core import (  # noqa: F401
    compare_tables,
    validate_finite_metric_bounds,
)
from tabular_drift_audit._pandas import compare_dataframes
from tabular_drift_audit.errors import (  # noqa: F401
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_INSUFFICIENT_NONMISSING,
    AVAILABILITY_UNAVAILABLE_SCHEMA,
    STATUS_COMPLETE,
    STATUS_INVALID_INPUT,
    STATUS_NUMERICALLY_INDETERMINATE,
    STATUS_UNKNOWN_BUDGET,
    STATUS_UNSUPPORTED_INPUT,
    DriftError,
)
from tabular_drift_audit.plan import (  # noqa: F401
    DEFAULT_BUDGET,
    HARD_MAX_CATEGORY_UNION_PER_FIELD,
    HARD_MAX_CATEGORY_UTF8_BYTES,
    HARD_MAX_CUTPOINTS_PER_FIELD,
    HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD,
    HARD_MAX_FIELD_NAME_BYTES,
    HARD_MAX_KS_SWEEP_STEPS,
    HARD_MAX_KS_UNION_POINTS_PER_FIELD,
    HARD_MAX_NUMERIC_BINS_PER_FIELD,
    HARD_MAX_REPORT_BYTES,
    HARD_MAX_RETAINED_ARROW_BYTES,
    HARD_MAX_ROWS_PER_DATASET,
    HARD_MAX_SELECTED_FIELDS,
    HARD_MAX_SOURCE_FIELDS_PER_DATASET,
    HARD_MAX_VALUE_VISITS,
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
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_INSUFFICIENT_NONMISSING",
    "AVAILABILITY_UNAVAILABLE_SCHEMA",
    "STATUS_COMPLETE",
    "STATUS_INVALID_INPUT",
    "STATUS_NUMERICALLY_INDETERMINATE",
    "STATUS_UNKNOWN_BUDGET",
    "STATUS_UNSUPPORTED_INPUT",
    "HARD_MAX_ROWS_PER_DATASET",
    "HARD_MAX_SOURCE_FIELDS_PER_DATASET",
    "HARD_MAX_SELECTED_FIELDS",
    "HARD_MAX_FIELD_NAME_BYTES",
    "HARD_MAX_CUTPOINTS_PER_FIELD",
    "HARD_MAX_NUMERIC_BINS_PER_FIELD",
    "HARD_MAX_CATEGORY_UTF8_BYTES",
    "HARD_MAX_CATEGORY_UNION_PER_FIELD",
    "HARD_MAX_DICTIONARY_ENTRIES_PER_FIELD",
    "HARD_MAX_RETAINED_ARROW_BYTES",
    "HARD_MAX_VALUE_VISITS",
    "HARD_MAX_KS_SWEEP_STEPS",
    "HARD_MAX_KS_UNION_POINTS_PER_FIELD",
    "HARD_MAX_REPORT_BYTES",
    "NUMERIC_ABS_LIMIT",
    "DEFAULT_BUDGET",
    "DriftError",
    "ExactFraction",
    "ColumnPlan",
    "ComparisonPlan",
    "Budget",
    "FieldSchemaComparison",
    "SchemaReport",
    "MissingnessReport",
    "NumericMetricReport",
    "CategoricalMetricReport",
    "ColumnReport",
    "LogicalWorkReport",
    "ComparisonReport",
    "compare_tables",
    "compare_dataframes",
    "validate_finite_metric_bounds",
]
