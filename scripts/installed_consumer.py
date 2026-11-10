"""Independent installed-artifact smoke/goldens; no source checkout fallback."""
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pyarrow as pa
import tabular_drift_audit as drift

if not Path(drift.__file__).is_relative_to("/opt/runtime"):
    raise RuntimeError("Consumer did not load the installed runtime artifact")
for name in ["pandas", "build", "setuptools", "wheel"]:
    if importlib.util.find_spec(name) is not None:
        raise RuntimeError("Unexpected runtime build/optional dependency: " + name)
if any(shutil.which(name) for name in ["gcc", "cc", "g++", "clang", "rustc"]):
    raise RuntimeError("Runtime unexpectedly contains a compiler")
if Path("/work/project").exists() or Path("/work/source").exists():
    raise RuntimeError("Source checkout present in runtime")
plan = drift.ComparisonPlan([drift.ColumnPlan("x", "numeric", [0]),
                             drift.ColumnPlan("category", "categorical")])
reference = pa.table({"x": [-1., 0.], "category": ["a", "a"]})
current = pa.table({"x": [1., 2.], "category": ["b", "b"]})
report = drift.compare_tables(reference, current, plan)
if report.column("x").numeric.ks_distance.to_fraction() != Fraction(1):
    raise RuntimeError("Independent disjoint KS golden failed")
if abs(report.column("x").numeric.psi - 2.14591721657880049946767911096825) > 1e-13:
    raise RuntimeError("Independent PSI literal failed")
if report.column("category").categorical.js_divergence_bits != 1:
    raise RuntimeError("Independent disjoint JSD golden failed")
saved = report.to_json_bytes(); export = report.to_dict(); export["complete"] = False
if report.to_json_bytes() != saved or not json.loads(saved)["complete"]:
    raise RuntimeError("Immutable report export failed")
print(json.dumps({"status": "INSTALLED_RUNTIME_CONSUMER_COMPLETE", "version": drift.__version__,
                  "package_path": drift.__file__, "KS": [1, 1], "JSD_bits": 1}))
