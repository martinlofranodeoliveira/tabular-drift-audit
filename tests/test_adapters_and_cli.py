"""Real installed CLI codecs, file safety, strict JSON and optional pandas tests."""
from fractions import Fraction
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from tabular_drift_audit import (Budget, ColumnPlan, ComparisonPlan, DriftError,
                                compare_dataframes, compare_tables)


class TestAdaptersAndCLI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="drift-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.plan = self.directory / "plan.json"
        self.plan.write_text('{"columns":[{"name":"x","kind":"numeric","cutpoints":[0,1]},{"name":"cat","kind":"categorical"}]}')

    def cli(self, *arguments, isolated=True):
        environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/local/bin:/usr/bin:/bin",
                       "HOME": "/work/home", "TMPDIR": "/work/tmp", "LANG": "C.UTF-8",
                       "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                       "ARROW_NUM_THREADS": "1"}
        command = ([sys.executable, "-I", "-m", "tabular_drift_audit"] if isolated else
                   [str(Path(sys.executable).parent / "tabular-drift-audit")])
        return subprocess.run([*command, *arguments],
                              cwd=self.directory, env=environment, capture_output=True)

    def compare(self, reference, current, reference_format="csv", current_format="parquet", *extra, isolated=True):
        return self.cli("compare", "--reference", str(reference), "--current", str(current),
                         "--reference-format", reference_format, "--current-format", current_format,
                         "--plan", str(self.plan), *extra, isolated=isolated)

    def failure(self, process, status, reason):
        self.assertEqual(process.returncode, 3 if status == "UNKNOWN_BUDGET" else 2, process.stderr)
        self.assertEqual(process.stdout, b"")
        record = json.loads(process.stderr)
        self.assertEqual(record, {"status": status, "complete": False, "reason_code": reason})

    def test_installed_cli_all_codecs_equal_core_and_strict_csv_extras(self):
        reference = self.directory / "reference.csv"
        reference.write_bytes(b'x,cat,extra\n0,a,001\n1,"",NA\n2,,NaN\n3,NaN,last\n')
        current = pa.table({"x": pa.array([2., 3., 4., None]), "cat": ["b", "b", "c", None],
                            "extra": ["001", "NA", "NaN", "last"]})
        core_reference = pa.table({"x": [0., 1., 2., 3.], "cat": ["a", "", None, "NaN"],
                                   "extra": ["001", "NA", "NaN", "last"]})
        expected = compare_tables(core_reference, current,
            ComparisonPlan([ColumnPlan("x", "numeric", [0, 1]), ColumnPlan("cat", "categorical")]))
        for codec in ["NONE", "SNAPPY", "ZSTD"]:
            with self.subTest(codec=codec):
                file = self.directory / (codec + ".parquet")
                pq.write_table(current, file, compression=codec, use_dictionary=["cat", "extra"],
                                row_group_size=2, write_page_checksum=True)
                process = self.compare(reference, file)
                self.assertEqual(process.returncode, 0, process.stderr)
                actual = json.loads(process.stdout)
                wanted = expected.to_dict()
                actual.pop("work"); wanted.pop("work")
                self.assertEqual(actual, wanted)
                self.assertEqual(actual["schema"]["incompatible_type_changes"], [])

    def test_zero_row_and_blank_row_csv(self):
        self.plan.write_text('{"columns":[{"name":"x","kind":"numeric"}]}')
        left = self.directory / "left.csv"; right = self.directory / "right.csv"
        left.write_bytes(b"x\n"); right.write_bytes(b"x\n\n1\n")
        process = self.compare(left, right, "csv", "csv")
        self.assertEqual(process.returncode, 0, process.stderr)
        data = json.loads(process.stdout)
        self.assertEqual((data["reference_row_count"], data["current_row_count"]), (0, 2))
        self.assertEqual(data["columns"][0]["numeric"]["availability"], "INSUFFICIENT_NONMISSING")
        self.assertIsNone(data["columns"][0]["missingness"]["reference_missing_rate"])
        self.assertEqual(data["columns"][0]["missingness"]["current_missing_count"], 1)

    def test_strict_plan_json_and_csv_errors(self):
        file = self.directory / "input.csv"; file.write_text("x,cat\n1,a\n")
        for text, reason in [
            ('{"columns":[],"columns":[]}', "DUPLICATE_JSON_KEY"),
            ('{"columns":[{"name":"x","kind":"numeric","cutpoints":[NaN]}]}', "NONFINITE_JSON_NUMBER"),
            ('{"columns":[],"unexpected":1}', "INVALID_PLAN_SHAPE"),
        ]:
            self.plan.write_text(text)
            self.failure(self.compare(file, file, "csv", "csv"), "INVALID_INPUT", reason)
        self.plan.write_text('{"columns":[{"name":"x","kind":"numeric"}]}')
        file.write_bytes(b"x,x\n1,2\n")
        self.failure(self.compare(file, file, "csv", "csv"), "INVALID_INPUT", "DUPLICATE_OR_EMPTY_CSV_HEADER")
        file.write_bytes(b"x\nnot-a-number\n")
        self.failure(self.compare(file, file, "csv", "csv"), "INVALID_INPUT", "CSV_PARSE_ERROR")
        file.write_bytes(b"x\n1\n2\n")
        self.failure(self.compare(file, file, "csv", "csv", "--max-rows", "1"),
                     "UNKNOWN_BUDGET", "ROWS_PER_DATASET_BUDGET_EXCEEDED")
        self.failure(self.compare(file, file, "csv", "csv", "--max-output-bytes", "1"),
                     "UNKNOWN_BUDGET", "REPORT_BYTES_BUDGET_EXCEEDED")

    def test_fifo_symlink_directory_and_input_byte_bounds(self):
        regular = self.directory / "data.csv"; regular.write_text("x,cat\n1,a\n")
        fifo = self.directory / "fifo"; os.mkfifo(fifo)
        link = self.directory / "link"; link.symlink_to(regular)
        self.failure(self.compare(fifo, regular, "csv", "csv"), "UNSUPPORTED_INPUT", "NON_REGULAR_INPUT")
        self.failure(self.compare(link, regular, "csv", "csv"), "UNSUPPORTED_INPUT", "SYMLINK_INPUT_UNSUPPORTED")
        self.failure(self.compare(self.directory, regular, "csv", "csv"), "UNSUPPORTED_INPUT", "NON_REGULAR_INPUT")
        oversized = self.directory / "oversized"
        with oversized.open("wb") as out:
            out.truncate(8_388_609)
        self.failure(self.compare(oversized, regular, "csv", "csv"), "UNKNOWN_BUDGET", "INPUT_BYTES_EXCEEDED")

    def test_corrupt_compressed_pages_truncation_and_unsupported_codec(self):
        reference = self.directory / "ref.csv"; reference.write_text("x,cat\n0,a\n")
        table = pa.table({"x": np.random.default_rng(410).normal(size=1024), "cat": ["a"] * 1024})
        for codec in ["SNAPPY", "ZSTD"]:
            file = self.directory / (codec + ".parquet")
            pq.write_table(table, file, compression=codec, use_dictionary=["cat"],
                            write_page_checksum=True, write_statistics=False)
            metadata = pq.ParquetFile(file).metadata
            offset = metadata.row_group(0).column(0).data_page_offset + 64
            data = bytearray(file.read_bytes()); data[offset] ^= 1; file.write_bytes(data)
            self.failure(self.compare(reference, file), "INVALID_INPUT", "PARQUET_DECODE_ERROR")
        file.write_bytes(bytes(data[:-12]))
        self.failure(self.compare(reference, file), "INVALID_INPUT", "PARQUET_DECODE_ERROR")
        pq.write_table(table.slice(0, 2), file, compression="gzip")
        self.failure(self.compare(reference, file), "UNSUPPORTED_INPUT", "PARQUET_CODEC_UNSUPPORTED")

    def test_fresh_cli_rejects_unknown_and_legacy_extensions(self):
        class SyntheticExtension(pa.ExtensionType):
            def __init__(self):
                super().__init__(pa.int64(), "synthetic.drift.extension")
            def __arrow_ext_serialize__(self):
                return b"fixed synthetic metadata"
            @classmethod
            def __arrow_ext_deserialize__(cls, storage, serialized):
                raise AssertionError("Fresh CLI must not register this test callback")
        extension = SyntheticExtension()
        table = pa.table({"x": pa.ExtensionArray.from_storage(extension, pa.array([1])), "cat": ["a"]})
        file = self.directory / "extension.parquet"; pq.write_table(table, file)
        reference = self.directory / "ref.csv"; reference.write_text("x,cat\n1,a\n")
        for isolated in (True, False):
            self.failure(self.compare(reference, file, isolated=isolated), "UNSUPPORTED_INPUT", "EXTENSION_METADATA_UNSUPPORTED")
        schema = pa.schema([pa.field("x", pa.int64(), metadata={
            b"ARROW:extension:name": b"arrow.py_extension_type", b"ARROW:extension:metadata": b"invalid-pickle"})])
        pq.write_table(pa.Table.from_arrays([pa.array([1])], schema=schema), file)
        for isolated in (True, False):
            self.failure(self.compare(reference, file, isolated=isolated), "UNSUPPORTED_INPUT", "EXTENSION_METADATA_UNSUPPORTED")

    def test_optional_pandas_allowlist_nullable_and_unused_categories(self):
        frame = pd.DataFrame({"x": pd.Series([1, None, 3], dtype="Int64"),
                              "cat": pd.Series(pd.Categorical(["a", None, "NaN"], categories=["unused", "a", "NaN"]))})
        before = frame.copy(deep=True)
        plan = ComparisonPlan([ColumnPlan("x", "numeric"), ColumnPlan("cat", "categorical")])
        report = compare_dataframes(frame, frame.copy(), plan)
        self.assertTrue(frame.equals(before))
        self.assertEqual(report.column("cat").categorical.union_cardinality, 2)
        self.assertEqual(report.column("x").missingness.reference_missing_count, 1)
        self.assertEqual(report.column("cat").categorical.js_divergence_bits, 0)
        for bad in [pd.DataFrame({"x": [True]}), pd.DataFrame({"x": pd.Series(["a", 1], dtype=object)})]:
            with self.assertRaises(DriftError) as caught:
                compare_dataframes(bad, bad, plan)
            self.assertEqual(caught.exception.status, "UNSUPPORTED_INPUT")
        excessive = pd.DataFrame({"cat": pd.Categorical(["a"], categories=["a"] + [str(i) for i in range(128)])})
        with self.assertRaises(DriftError) as caught:
            compare_dataframes(excessive, excessive, ComparisonPlan([ColumnPlan("cat", "categorical")]))
        self.assertEqual(caught.exception.reason_code, "PANDAS_CATEGORY_LEVELS_EXCEEDED")

    def test_demo_asserts_real_cross_format_comparison(self):
        process = self.cli("demo", "--assert")
        self.assertEqual(process.returncode, 0, process.stderr)
        record = json.loads(process.stdout)
        self.assertTrue(record["complete"])
        columns = {item["name"]: item for item in record["columns"]}
        self.assertEqual(columns["score"]["numeric"]["ks_distance"]["numerator"], 1)
        self.assertEqual(columns["score"]["numeric"]["ks_distance"]["denominator"], 2)

    def test_output_failure_never_returns_complete_success(self):
        from tabular_drift_audit import cli
        class RefuseOutput:
            def write(self, data):
                return 0
            def flush(self):
                pass
        fake = SimpleNamespace(to_json_bytes=lambda: b'{"complete":true}')
        diagnostics = io.StringIO()
        with patch.object(cli, "_demo", return_value=fake), \
             patch.object(cli.sys, "stdout", SimpleNamespace(buffer=RefuseOutput())), \
             patch.object(cli.sys, "stderr", diagnostics):
            self.assertEqual(cli.main(["demo", "--assert"]), 2)
        self.assertEqual(json.loads(diagnostics.getvalue()), {
            "status": "IO_ERROR", "complete": False, "reason_code": "OUTPUT_IO_ERROR"})
