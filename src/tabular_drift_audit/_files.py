"""Private CLI-only readers; no shared-interpreter SDK file-reader contract."""
from dataclasses import dataclass
import csv
import errno
import io
import json
import os
import re
import stat
import sys

import pyarrow as pa
import pyarrow.csv as arrow_csv
import pyarrow.parquet as parquet

from .core import (_ScannedFieldData, WorkLedger, _finish_comparison,
                   _scan_table_fields, _validate_source_schema)
from .errors import DriftError, STATUS_INVALID_INPUT, STATUS_UNKNOWN_BUDGET, STATUS_UNSUPPORTED_INPUT
from .plan import Budget, ColumnPlan, ComparisonPlan, validate_ascii_field_name

MAX_FILE_BYTES = 8_388_608
MAX_PLAN_BYTES = 65_536
MAX_HEADER_BYTES = 4096
MAX_ROW_GROUPS = 64
MAX_UNCOMPRESSED_BYTES = 33_554_432


class FileReadError(Exception):
    """Observed local I/O failure, separate from mathematical/input outcomes."""
    status = "IO_ERROR"
    reason_code = "FILE_IO_ERROR"


def read_regular(path: str, limit: int) -> bytes:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path):
        raise DriftError(STATUS_UNSUPPORTED_INPUT, "REMOTE_URI_UNSUPPORTED")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise DriftError(STATUS_UNSUPPORTED_INPUT, "SYMLINK_INPUT_UNSUPPORTED") from None
        raise FileReadError() from None
    except ValueError:
        raise FileReadError() from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DriftError(STATUS_UNSUPPORTED_INPUT, "NON_REGULAR_INPUT")
        if metadata.st_size > limit:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "INPUT_BYTES_EXCEEDED")
        result = bytearray()
        while len(result) <= limit:
            chunk = os.read(descriptor, min(65_536, limit + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
        raise DriftError(STATUS_UNKNOWN_BUDGET, "INPUT_BYTES_EXCEEDED")
    except OSError:
        raise FileReadError() from None
    finally:
        os.close(descriptor)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DriftError(STATUS_INVALID_INPUT, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_plan(raw: bytes) -> ComparisonPlan:
    if len(raw) > MAX_PLAN_BYTES:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "PLAN_BYTES_EXCEEDED")
    def invalid_constant(value):
        raise DriftError(STATUS_INVALID_INPUT, "NONFINITE_JSON_NUMBER")
    try:
        value = json.loads(raw.decode("utf8"), object_pairs_hook=_unique_object,
                           parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise DriftError(STATUS_INVALID_INPUT, "MALFORMED_PLAN_JSON") from None
    if not isinstance(value, dict) or set(value) != {"columns"} or not isinstance(value["columns"], list):
        raise DriftError(STATUS_INVALID_INPUT, "INVALID_PLAN_SHAPE")
    if len(value["columns"]) > 16:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "SELECTED_FIELDS_EXCEEDED")
    columns = []
    for column in value["columns"]:
        if not isinstance(column, dict) or not {"name", "kind"} <= set(column) or set(column) - {"name", "kind", "cutpoints"}:
            raise DriftError(STATUS_INVALID_INPUT, "INVALID_COLUMN_PLAN_SHAPE")
        cuts = column.get("cutpoints", [])
        if not isinstance(cuts, list):
            raise DriftError(STATUS_INVALID_INPUT, "INVALID_CUTPOINTS_CONTAINER")
        columns.append(ColumnPlan(column["name"], column["kind"], cuts))
    return ComparisonPlan(columns)


def _csv_failure(error):
    if "straddl" in str(error).lower():
        return DriftError(STATUS_UNKNOWN_BUDGET, "CSV_BLOCK_BYTES_EXCEEDED")
    return DriftError(STATUS_INVALID_INPUT, "CSV_PARSE_ERROR")


@dataclass
class _Source:
    specs: dict
    reader: object
    batches: object
    rows_hint: int | None

    def close(self):
        self.reader.close()


def _csv_source(raw, plan, budget):
    if raw.startswith((b"\x1f\x8b", b"\x28\xb5\x2f\xfd", b"PK\x03\x04")):
        raise DriftError(STATUS_UNSUPPORTED_INPUT, "CSV_COMPRESSION_UNSUPPORTED")
    terminators = (raw.find(b"\r", 0, MAX_HEADER_BYTES + 1),
                   raw.find(b"\n", 0, MAX_HEADER_BYTES + 1))
    end = min((position for position in terminators if position >= 0), default=-1)
    if end < 0:
        if len(raw) > MAX_HEADER_BYTES:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "CSV_HEADER_BYTES_EXCEEDED")
        end = len(raw)
    if end > MAX_HEADER_BYTES:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "CSV_HEADER_BYTES_EXCEEDED")
    try:
        names = next(csv.reader(io.StringIO(raw[:end].decode("utf8"), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error, StopIteration):
        raise DriftError(STATUS_INVALID_INPUT, "CSV_HEADER_INVALID") from None
    if len(names) > budget.max_source_fields_per_dataset:
        raise DriftError(STATUS_UNKNOWN_BUDGET, "SOURCE_FIELDS_BUDGET_EXCEEDED")
    if not names or len(set(names)) != len(names):
        raise DriftError(STATUS_INVALID_INPUT, "DUPLICATE_OR_EMPTY_CSV_HEADER")
    for name in names:
        validate_ascii_field_name(name, budget.max_field_name_bytes)
    kinds = {column.name: column.kind for column in plan.columns}
    types = {name: pa.float64() if kinds.get(name) == "numeric" else pa.string() for name in names}
    schema = pa.schema([(name, types[name]) for name in names])
    specs = _validate_source_schema(schema, 0, "CSV", budget)
    body_start = end + (2 if raw[end:end + 2] == b"\r\n" else 1)
    if not raw[body_start:]:
        reader = pa.RecordBatchReader.from_batches(schema, [])
        return _Source(specs, reader, iter(()), 0)
    try:
        reader = arrow_csv.open_csv(
            pa.BufferReader(raw),
            read_options=arrow_csv.ReadOptions(column_names=names, skip_rows=1, use_threads=False, block_size=65_536),
            parse_options=arrow_csv.ParseOptions(ignore_empty_lines=False, newlines_in_values=False),
            convert_options=arrow_csv.ConvertOptions(column_types=types, null_values=[""],
                                                      strings_can_be_null=True, quoted_strings_can_be_null=False))
    except (pa.ArrowInvalid, OSError) as error:
        raise _csv_failure(error) from None
    def batches():
        try:
            for batch in reader:
                for offset in range(0, batch.num_rows, budget.max_batch_rows):
                    yield batch.slice(offset, budget.max_batch_rows)
        except (pa.ArrowInvalid, OSError) as error:
            raise _csv_failure(error) from None
    return _Source(specs, reader, batches(), None)


def _parquet_source(raw, plan, budget):
    if raw[:4] == b"PARE" or raw[-4:] == b"PARE":
        raise DriftError(STATUS_UNSUPPORTED_INPUT, "PARQUET_ENCRYPTION_UNSUPPORTED")
    options = dict(thrift_string_size_limit=65_536, thrift_container_size_limit=4096,
                   page_checksum_verification=True, arrow_extensions_enabled=False,
                   memory_map=False)
    original = None
    try:
        original = parquet.ParquetFile(pa.BufferReader(raw), **options)
        metadata = original.metadata
        if metadata.num_rows > budget.max_rows_per_dataset:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "ROWS_PER_DATASET_BUDGET_EXCEEDED")
        if metadata.num_row_groups > MAX_ROW_GROUPS or metadata.num_columns > budget.max_source_fields_per_dataset:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "PARQUET_METADATA_BUDGET_EXCEEDED")
        total = 0
        for row_group in range(metadata.num_row_groups):
            group = metadata.row_group(row_group)
            for index in range(group.num_columns):
                column = group.column(index)
                if column.file_path:
                    raise DriftError(STATUS_UNSUPPORTED_INPUT, "PARQUET_EXTERNAL_COLUMN_REFERENCE")
                if column.compression not in {"UNCOMPRESSED", "SNAPPY", "ZSTD"}:
                    raise DriftError(STATUS_UNSUPPORTED_INPUT, "PARQUET_CODEC_UNSUPPORTED")
                if column.total_uncompressed_size < 0:
                    raise DriftError(STATUS_INVALID_INPUT, "PARQUET_METADATA_INVALID")
                total += column.total_uncompressed_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise DriftError(STATUS_UNKNOWN_BUDGET, "PARQUET_UNCOMPRESSED_BYTES_EXCEEDED")
        schema = original.schema_arrow
        specs = _validate_source_schema(schema, metadata.num_rows, "Parquet", budget)
        # Dictionary read conversion is an internal representation; source specs
        # above retain the actual source schema, not the requested read encoding.
        dictionaries = [name for name, spec in specs.items() if spec.logical_kind == "categorical"]
        reader = parquet.ParquetFile(pa.BufferReader(raw), read_dictionary=dictionaries, **options)
    except (pa.ArrowInvalid, OSError):
        raise DriftError(STATUS_INVALID_INPUT, "PARQUET_DECODE_ERROR") from None
    finally:
        if original is not None:
            original.close()
    def batches():
        try:
            yield from reader.iter_batches(batch_size=budget.max_batch_rows, use_threads=False,
                                           use_pandas_metadata=False)
        except (pa.ArrowInvalid, OSError):
            raise DriftError(STATUS_INVALID_INPUT, "PARQUET_DECODE_ERROR") from None
    return _Source(specs, reader, batches(), metadata.num_rows)


def _collect(source, selected, keep, unions, ledger, budget, retained):
    aggregate = {name: _ScannedFieldData(spec, 0, 0, 0,
                    [] if name in keep and spec.logical_kind == "numeric" else None,
                    [] if name in keep and spec.logical_kind == "categorical" else None)
                 for name, spec in source.specs.items()}
    rows = 0
    dictionaries = {}
    for batch in source.batches:
        rows += batch.num_rows
        if rows > budget.max_rows_per_dataset:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "ROWS_PER_DATASET_BUDGET_EXCEEDED")
        projected = sum(batch.column(index).nbytes for index, name in enumerate(batch.schema.names)
                        if name in selected)
        if projected > budget.max_projected_batch_bytes:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "PROJECTED_BATCH_BYTES_EXCEEDED")
        retained[0] += projected
        if retained[0] > budget.max_retained_arrow_bytes:
            raise DriftError(STATUS_UNKNOWN_BUDGET, "RETAINED_ARROW_BYTES_EXCEEDED")
        table = pa.Table.from_batches([batch])
        ledger.charge_visits(sum(table.column(i).num_chunks for i in range(table.num_columns)))
        part = _scan_table_fields(table, source.specs, keep, ledger, budget, unions, dictionaries)
        for name, item in part.items():
            dest = aggregate[name]
            dest.total_rows += item.total_rows
            dest.missing_count += item.missing_count
            dest.nonmissing_count += item.nonmissing_count
            if dest.numeric_values is not None:
                dest.numeric_values.extend(item.numeric_values)
            if dest.categorical_values is not None:
                dest.categorical_values.extend(item.categorical_values)
    if source.rows_hint is not None and rows != source.rows_hint:
        raise DriftError(STATUS_INVALID_INPUT, "PARQUET_ROW_COUNT_MISMATCH")
    return aggregate, rows


def compare_cli_files(reference_path, reference_format, current_path, current_format, plan, budget):
    """Private entrypoint called only by the fresh controlled CLI."""
    if "pandas" in sys.modules:
        raise DriftError(STATUS_UNSUPPORTED_INPUT, "UNCONTROLLED_INTERPRETER")
    sources = []
    try:
        for path, format_name in [(reference_path, reference_format), (current_path, current_format)]:
            raw = read_regular(path, MAX_FILE_BYTES)
            if format_name == "csv":
                sources.append(_csv_source(raw, plan, budget))
            elif format_name == "parquet":
                sources.append(_parquet_source(raw, plan, budget))
            else:
                raise DriftError(STATUS_UNSUPPORTED_INPUT, "FORMAT_UNSUPPORTED")
        reference, current = sources
        keep = {column.name for column in plan.columns
                if column.name in reference.specs and column.name in current.specs
                and reference.specs[column.name].logical_kind == column.kind
                and current.specs[column.name].logical_kind == column.kind}
        ledger = WorkLedger(budget)
        selected = set(plan.selected_names)
        unions = {name: set() for name in selected}
        retained = [0]
        ref_data, ref_rows = _collect(reference, selected, keep, unions, ledger, budget, retained)
        cur_data, cur_rows = _collect(current, selected, keep, unions, ledger, budget, retained)
        return _finish_comparison(reference.specs, current.specs, ref_data, cur_data,
                                  plan, budget, ledger, ref_rows, cur_rows)
    finally:
        for source in sources:
            source.close()
