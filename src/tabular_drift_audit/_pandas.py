"""Optional adapter for trusted, cooperatively owned DataFrames."""
from .errors import DriftError, STATUS_INVALID_INPUT, STATUS_UNKNOWN_BUDGET, STATUS_UNSUPPORTED_INPUT
from .plan import DEFAULT_BUDGET, Budget, ComparisonPlan, validate_ascii_field_name


def compare_dataframes(reference, current, plan, budget=DEFAULT_BUDGET):
    """Compare trusted pandas frames; pandas is imported only on this explicit call.

    Object/mixed and arbitrary extension dtypes are not inferred. Conversion may
    allocate native buffers before the core's byte admission checks.
    """
    if not isinstance(budget, Budget):
        raise DriftError(STATUS_INVALID_INPUT, "INVALID_BUDGET")
    if not isinstance(plan, ComparisonPlan):
        raise DriftError(STATUS_INVALID_INPUT, "INVALID_COMPARISON_PLAN")
    import pandas as pd
    import numpy as np
    import pyarrow as pa
    from .core import compare_tables
    tables = []
    for frame in (reference, current):
        if not isinstance(frame, pd.DataFrame):
            raise DriftError(STATUS_INVALID_INPUT, "EXPECTED_DATAFRAME")
        rows, fields = frame.shape
        if rows > budget.max_rows_per_dataset:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "ROWS_PER_DATASET_BUDGET_EXCEEDED")
        if fields > budget.max_source_fields_per_dataset:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "SOURCE_FIELDS_BUDGET_EXCEEDED")
        if not frame.columns.is_unique:
            raise DriftError(STATUS_INVALID_INPUT, "DUPLICATE_SOURCE_FIELD_NAME")
        for name, series in frame.items():
            validate_ascii_field_name(name, budget.max_field_name_bytes)
            dtype = series.dtype
            primitive = isinstance(dtype, np.dtype) and dtype in {
                np.dtype("int32"), np.dtype("int64"), np.dtype("float64")}
            nullable = type(dtype) in {pd.Int32Dtype, pd.Int64Dtype, pd.Float64Dtype}
            string = type(dtype) is pd.StringDtype
            categorical = type(dtype) is pd.CategoricalDtype
            if not (primitive or nullable or string or categorical):
                raise DriftError(STATUS_UNSUPPORTED_INPUT, "PANDAS_DTYPE_UNSUPPORTED")
            if categorical:
                if len(dtype.categories) > 128:
                    raise DriftError(STATUS_UNKNOWN_BUDGET, "PANDAS_CATEGORY_LEVELS_EXCEEDED")
                for label in dtype.categories:
                    if not isinstance(label, str):
                        raise DriftError(STATUS_UNSUPPORTED_INPUT, "PANDAS_CATEGORY_LABEL_TYPE")
                    try:
                        length = len(label.encode("utf8"))
                    except UnicodeEncodeError:
                        raise DriftError(STATUS_INVALID_INPUT, "INVALID_UTF8_STRING") from None
                    if length > budget.max_category_utf8_bytes:
                        raise DriftError(STATUS_UNKNOWN_BUDGET, "CATEGORY_BYTES_BUDGET_EXCEEDED")
        try:
            tables.append(pa.Table.from_pandas(frame, preserve_index=False))
        except (pa.ArrowInvalid, pa.ArrowTypeError, UnicodeEncodeError):
            raise DriftError(STATUS_INVALID_INPUT, "PANDAS_CONVERSION_INVALID") from None
    return compare_tables(tables[0], tables[1], plan, budget)
