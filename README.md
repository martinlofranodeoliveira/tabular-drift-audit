# Tabular Drift Audit

Development version **0.1.0**. Compare two small reference/current feature snapshots locally using exact empirical KS distance, frozen-bin PSI, categorical Jensen–Shannon divergence, missing rates and schema findings. The SDK and CLI share one statistics engine. No model, service, telemetry or runtime network access is used.

## Install and run

The qualified lock targets Linux x86-64 and Python 3.13. The original Python package uses NumPy 2.5.4, SciPy 1.18.1 and PyArrow 26.0.1.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/tabular-drift-audit demo --assert
.venv/bin/tabular-drift-audit compare --reference examples/reference.csv --reference-format csv --current examples/current.csv --current-format csv --plan examples/plan.json
```

The demo writes original synthetic CSV/Parquet inputs in a private temporary directory, checks the resulting distances/counts and removes its temporary files. JSON goes to stdout; errors with stable status/reason codes go to stderr. Exit 0 means a complete descriptive report, including any schema findings. It is not a healthy/drifted verdict. Input/unsupported/I/O errors exit 2, budget exhaustion exits 3 and numerical indeterminacy exits 4.

```python
import pyarrow as pa
from tabular_drift_audit import ColumnPlan, ComparisonPlan, compare_tables

plan = ComparisonPlan([ColumnPlan("score", "numeric", [1, 2])])
result = compare_tables(pa.table({"score": [0., 1., 2.]}),
                        pa.table({"score": [2., 3., 4.]}), plan)
print(result.column("score").numeric.ks_distance.to_fraction())
print(result.to_json())
```

`ColumnPlan`, `ComparisonPlan`, `Budget` and returned reports are immutable. Caller tables must be cooperatively owned with no concurrent mutation. The SDK accepts already-created Arrow tables, not file paths. For trusted DataFrames, install `.[pandas]` and explicitly call `compare_dataframes`; pandas is imported lazily. Object/mixed, bool, temporal and arbitrary extension dtypes are rejected.

## Interpret the report

- **KS:** exact reduced fraction for the maximum empirical CDF difference, handling all ties. No p-value or significance claim.
- **PSI:** the caller's unchanged right-closed bins, including under/overflow, with additive half-count smoothing and natural logarithms. Smoothing depends on sample size. No automatic bin fitting or conventional threshold is selected.
- **Categorical JSD:** unsmoothed divergence in bits, from 0 to 1. This is the divergence, not its square-root distance. Null is missing; empty string and literal `NaN` are categories. UTF-8 identity is exact without normalization.
- **Missing/schema:** exact missing-rate fractions and signed current-minus-reference change; added/dropped and compatible/incompatible types are explicit. Missing fields or a plan-kind mismatch make that distribution unavailable. Present-column missing rates remain available independently.

All-missing distributions are `INSUFFICIENT_NONMISSING`; zero-row rates are unavailable. No numeric sentinel replaces unavailable evidence. Reports contain field names and explicit cutpoints, not raw observations, category labels, input paths or timestamps.

## Supported files and bounds

CLI readers run only in a fresh, controlled, owned Python interpreter without pandas, plugins, user scripts or arbitrary extension registrations. They support cooperative regular local files; leaf symlinks, FIFO, directories, remote URIs and external Parquet column references are rejected. There is no shared-process SDK file-reader promise. See [the contract](docs/contract.md) for the registered-extension counterexample and the exact limits.

The normal console entrypoint trusts Python startup, including `PYTHONPATH`, user-site and `sitecustomize`; those must belong to the controlled environment. `python -I -m tabular_drift_audit ...` excludes additional startup inputs but does not sandbox arbitrary installed packages/registries. Callback-zero evidence is specific to the qualified owned interpreter and packages, not a universal pre-parser protection. SDK Arrow tables and optional DataFrames are trusted, already-created Python objects; their data is still validated.

CSV uses an explicit plan-driven numeric/string mapping, including string extras. Unquoted empty fields are null; quoted empty fields are strings; blank rows are preserved. Quoted multiline fields/headers and compressed CSV are outside the subset. Parquet admits flat int32/int64/float64, UTF-8/large UTF-8 and integer-indexed UTF-8 dictionaries with UNCOMPRESSED, SNAPPY or ZSTD codecs. Nested, encrypted, extension and other logical types/codecs are unsupported.

The main caps are 8 MiB per file, 16,384 rows per side, 32 source/16 selected fields, 128 observed categories per selected field, 256 UTF-8 bytes per string, 15 cutpoints, 64 MiB of admitted selected Arrow bytes and 8 MiB of JSON. Native parser/conversion allocations can precede application checks; logical limits do not establish a universal RSS or deadline guarantee. Unfinished/OOM/timeout work is **INCOMPLETE**, not a statistical negative.

## Reproducible checks

```sh
sh scripts/check.sh
```

This prepares only the exact official artifacts in `locks/artifacts.json`, then uses fresh offline containers without host binds: UID/GID 1000, no capabilities, no-new-privileges, 256 PIDs, 1 GiB and 2 CPUs. Native tests include all 1,156 small multiset pairs against an independent Fraction CDF reference, 70-digit Decimal goldens, malformed inputs, budget seams and installed CLI codec tests. A separate runtime consumes the real wheel without the source checkout, build tools, pandas or a compiler. Per-file source/package hashes and actual container evidence are written under `build/`. Runtime tests also use a read-only root and a bounded temporary filesystem.

See [API](docs/api.md), [design](docs/design.md), [Português](README.pt-BR.md) and [license boundaries](THIRD_PARTY.md). MIT covers original Lofrano source; dependencies and bundled native libraries retain their own notices.
