"""Installed fresh CLI regressions for physical CSV header terminators."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TestCSVRecordTerminators(unittest.TestCase):
    def test_csv_header_record_terminators_and_physical_bounds(self):
        basic_plan = {"columns": [{"name": "x", "kind": "numeric"},
                                   {"name": "cat", "kind": "categorical"}]}
        cases = []
        for terminator in [b"\n", b"\r\n", b"\r"]:
            cases.append(("two_rows_" + repr(terminator),
                          b"x,cat" + terminator + b"1,a" + terminator + b"2,b",
                          basic_plan, 2, None))
        cases += [("mixed_CR_LF", b"x,cat\r1,a\n", basic_plan, 1, None),
                  ("mixed_all", b"x,cat\r1,a\n2,b\r\n3,c", basic_plan, 3, None)]
        for terminator in [b"", b"\n", b"\r\n", b"\r"]:
            cases.append(("header_only_" + repr(terminator), b"x,cat" + terminator,
                          basic_plan, 0, None))

        #32 valid64-byte unique field names, with quoting making the physical
        #header exactly4096 bytes. This tests the real bound independently of
        #the separate decoded-name and field-count limits.
        names = [f"{index:02d}" + '"' * 62 for index in range(32)]
        names[0] = names[0].replace('"', 'a', 31)
        encode_header = lambda values: ",".join('"' + name.replace('"', '""') + '"' for name in values).encode("ascii")
        header = encode_header(names)
        self.assertEqual(len(header), 4096)
        wide_plan = {"columns": [{"name": names[0], "kind": "numeric"}]}
        row = b",".join([b"1"] + [b"a"] * 31)
        for terminator in [b"", b"\n", b"\r\n", b"\r"]:
            cases.append(("exact_header_bound_" + repr(terminator),
                          header + terminator + (row if terminator else b""),
                          wide_plan, 1 if terminator else 0, None))
        oversized_names = list(names)
        oversized_names[0] = oversized_names[0].replace('a', '"', 1)
        oversized = encode_header(oversized_names)
        self.assertEqual(len(oversized), 4097)
        for terminator in [b"", b"\n", b"\r\n", b"\r"]:
            cases.append(("over_header_bound_" + repr(terminator),
                          oversized + terminator + (row if terminator else b""),
                          wide_plan, None, "CSV_HEADER_BYTES_EXCEEDED"))

        with tempfile.TemporaryDirectory(prefix="drift-terminators-") as temporary:
            directory = Path(temporary)
            reference = directory / "reference.csv"
            current = directory / "current.csv"
            plan_file = directory / "plan.json"
            environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/local/bin:/usr/bin:/bin",
                           "HOME": temporary, "TMPDIR": temporary, "LANG": "C.UTF-8",
                           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                           "MKL_NUM_THREADS": "1", "ARROW_NUM_THREADS": "1"}
            for name, raw, plan, rows, reason in cases:
                with self.subTest(case=name):
                    reference.write_bytes(raw); current.write_bytes(raw)
                    plan_file.write_text(json.dumps(plan))
                    completed = subprocess.run([sys.executable, "-I", "-m", "tabular_drift_audit",
                        "compare", "--reference", str(reference), "--current", str(current),
                        "--reference-format", "csv", "--current-format", "csv", "--plan", str(plan_file)],
                        cwd=temporary, env=environment, capture_output=True, timeout=15)
                    if reason:
                        self.assertEqual(completed.returncode, 3, completed.stderr)
                        self.assertEqual(completed.stdout, b"")
                        self.assertEqual(json.loads(completed.stderr),
                            {"status": "UNKNOWN_BUDGET", "complete": False, "reason_code": reason})
                    else:
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        self.assertEqual(completed.stderr, b"")
                        report = json.loads(completed.stdout)
                        self.assertTrue(report["complete"])
                        self.assertEqual(report["reference_row_count"], rows)
                        self.assertEqual(report["current_row_count"], rows)


if __name__ == "__main__":
    unittest.main()
