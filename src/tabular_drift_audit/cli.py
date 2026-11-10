"""Fresh-process local-file command line adapter."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import tempfile

from . import __version__
from ._files import (FileReadError, MAX_PLAN_BYTES, compare_cli_files, parse_plan,
                     read_regular)
from .errors import DriftError
from .plan import Budget, ColumnPlan, ComparisonPlan


def _emit(payload):
    remaining = memoryview(payload + b"\n")
    while remaining:
        written = sys.stdout.buffer.write(remaining)
        if written is None or written <= 0:
            raise OSError("Incomplete output")
        remaining = remaining[written:]
    sys.stdout.buffer.flush()


def _failure(status, reason):
    try:
        sys.stderr.write(json.dumps({"status": status, "complete": False, "reason_code": reason},
                                    separators=(",", ":")) + "\n")
        sys.stderr.flush()
    except OSError:
        pass
    return {"UNKNOWN_BUDGET": 3, "NUMERICALLY_INDETERMINATE": 4}.get(status, 2)


def _demo(check):
    # Fixed original fixtures, not a reader of user paths or arbitrary programs.
    import pyarrow as pa
    import pyarrow.csv as csv
    import pyarrow.parquet as parquet
    with tempfile.TemporaryDirectory(prefix="tabular-drift-demo-") as temporary:
        directory = Path(temporary)
        ref = b"score,kind\n0,a\n1,a\n2,b\n3,\n"
        cur = b"score,kind\n2,b\n3,b\n4,c\n,\n"
        reference = directory / "reference.csv"
        current = directory / "current.parquet"
        reference.write_bytes(ref)
        table = csv.read_csv(pa.BufferReader(cur), read_options=csv.ReadOptions(use_threads=False),
            convert_options=csv.ConvertOptions(column_types={"score": pa.float64(), "kind": pa.string()},
                null_values=[""], strings_can_be_null=True, quoted_strings_can_be_null=False))
        parquet.write_table(table, current, compression="snappy", use_dictionary=["kind"],
                            row_group_size=2, write_page_checksum=True)
        plan = ComparisonPlan([ColumnPlan("score", "numeric", [1, 2]),
                               ColumnPlan("kind", "categorical")])
        report = compare_cli_files(str(reference), "csv", str(current), "parquet", plan, Budget())
        if check and not (report.column("score").numeric.ks_distance.to_fraction() == Fraction(1, 2)
                          and report.column("score").missingness.missing_rate_delta.to_fraction() == Fraction(1, 4)
                          and report.column("kind").categorical.current_only_cardinality == 1):
            raise RuntimeError("Synthetic demo verification failed")
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded descriptive comparison of two local feature snapshots.")
    parser.add_argument("--version", action="version", version="tabular-drift-audit " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="Compare explicitly named local CSV/Parquet files")
    compare.add_argument("--reference", required=True)
    compare.add_argument("--current", required=True)
    compare.add_argument("--reference-format", required=True, choices=["csv", "parquet"])
    compare.add_argument("--current-format", required=True, choices=["csv", "parquet"])
    compare.add_argument("--plan", required=True)
    compare.add_argument("--max-rows", type=int, default=16384)
    compare.add_argument("--max-output-bytes", type=int, default=8388608)
    demo = commands.add_parser("demo", help="Run an original synthetic CSV/Parquet comparison")
    demo.add_argument("--assert", dest="assert_demo", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "demo":
            report = _demo(arguments.assert_demo)
        else:
            budget = Budget(max_rows_per_dataset=arguments.max_rows,
                            max_report_bytes=arguments.max_output_bytes)
            plan = parse_plan(read_regular(arguments.plan, MAX_PLAN_BYTES))
            report = compare_cli_files(arguments.reference, arguments.reference_format,
                                       arguments.current, arguments.current_format, plan, budget)
        _emit(report.to_json_bytes())
        return 0
    except (DriftError, FileReadError) as error:
        return _failure(error.status, error.reason_code)
    except OSError:
        # A truncated/failed stdout is never reported as a complete success.
        return _failure("IO_ERROR", "OUTPUT_IO_ERROR")


if __name__ == "__main__":
    raise SystemExit(main())
