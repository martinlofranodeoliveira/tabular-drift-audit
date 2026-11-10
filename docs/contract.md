# Contract

KS is `max_t |count(ref<=t)/n_ref - count(cur<=t)/n_cur|` over all observed finite union values after both tied groups advance. Integer cross-products are exact, with `n_ref*n_cur <= 2^28`; the result is reduced. Numeric null and NaN are excluded, infinity and magnitude above 1e12 are invalid, int32/int64 in that range convert exactly and signed zeros are equal. No continuous-distribution test or p-value is supplied.

PSI bins are `(-∞,c0], (c0,c1], ..., (c_last,+∞)`. For `K` bins, `p_i=(r_i+1/2)/(n_ref+K/2)` and `q_i=(c_i+1/2)/(n_cur+K/2)`; PSI is `Σ(p_i-q_i)ln(p_i/q_i)`. The caller freezes cutpoints; their selection provenance is not verified. The additive count policy depends on sample size: counts `[1,3]` versus `[2,6]` produce `ln(39/35)/45`, not zero.

Categorical JSD uses the full observed UTF-8 union, unsmoothed probabilities, their equal mixture and base-2 logarithms, with zero terms defined as zero. It reports divergence in `[0,1]`, not square-root distance. Strings are exact without Unicode normalization. Null is missing; empty string and literal `NaN` are data. Numeric and categorical missing rates are exact fractions over all rows, with signed current-minus-reference difference when both rates exist.

Distribution availability precedence is `UNAVAILABLE_SCHEMA`, `INSUFFICIENT_NONMISSING`, `AVAILABLE`. A missing selected source field is not invented as all-null. A present field's actual missing rate remains independent of a distribution kind mismatch. Schema field order and ordinary metadata are ignored. Dictionary indices are not category identities; string/large-string/dictionary and numeric int32/int64/float64 changes may remain compatible.

## Bounds

| Quantity | Hard cap |
| --- | ---: |
| Input file / plan / physical CSV header | 8 MiB / 64 KiB / 4096 bytes |
| Rows per dataset / source fields / selected fields | 16,384 / 32 / 16 |
| Field name / all source UTF-8 values | 64 ASCII bytes / 256 UTF-8 bytes |
| Observed union per selected text field / pandas declared levels | 128 / 128 |
| Dictionary entries per source field across chunks | 16,384 |
| Numeric cutpoints / bins | 15 / 16 |
| Parquet row groups / declared total uncompressed bytes | 64 / 32 MiB |
| Thrift string / container entries | 64 KiB / 4096 |
| Read batch rows / projected batch bytes | 256 / 4 MiB |
| Admitted selected Arrow bytes across both inputs | 64 MiB |
| Original visits / distinct KS union steps | 2,097,152 / 524,288 |
| Serialized JSON | 8 MiB |

Visits include source chunk headers, source cells (including null), dictionary entries (including unused), and selected nonmissing values mapped into bins/category counts. One shared ledger covers both inputs/all fields. Empty chunks are precharged. Dictionary repetition across physical chunks/batches is counted conservatively. CLI readers validate all fields and discard unselected data after validation, retaining selected values for the same statistics engine. Measured work and conservative Arrow-byte admission can depend on representation; they are not native allocator instrumentation.

## Files, extensions and completion

CLI files use owned cooperative local Linux directories, stable read leases and no concurrent hostile mutation. Leaf open uses NONBLOCK/NOFOLLOW/CLOEXEC, then fstat requires regular before reads. Retention is bounded by limit plus one sentinel and reads continue until EOF. Parent-path hostility, inherited-output stalls, power-loss durability and universal process cleanup are not promised. There is no persistent report overwrite operation.

CSV has explicit types for every field, strict width, no row skipping, no multiline quoted records/headers or compressed wrapper. Only unquoted empty fields are null. Header names are parsed by Python's maintained CSV parser; Arrow parses values. CSV logical types are declared by the plan, so malformed numeric tokens are input errors, not inferred schema-change findings.

Parquet supports only UNCOMPRESSED/SNAPPY/ZSTD flat admitted types. Metadata caps precede batches; page checksums are verified when present. External column references, nested/encrypted/extension types and other codecs are unsupported. Source schemas are captured before internal dictionary-read conversion, preventing a read option from fabricating a physical schema change.

File adapters are private to a fresh controlled CLI without pandas, plugins, user scripts or arbitrary extension registrations. `arrow_extensions_enabled=False` does **not** universally block generic callbacks: a registered custom extension can deserialize during `ParquetFile` construction. In the supported fresh process, unknown custom/legacy extension metadata remains storage metadata and is rejected before record batches. PyArrow 26.0.1 has no public/lib `PyExtensionType` auto-pickle loader. Arbitrary preloaded registries are outside the contract; normal `ARROW:schema` metadata is not blanket-rejected.

The ordinary installed console entrypoint trusts Python startup state: `PYTHONPATH`, user-site, `sitecustomize` and installed packages must be controlled. `-I -m` is a separately exercised startup mode, not a registry sandbox. Callback-zero evidence is limited to the particular fresh owned interpreter/package environment. A registered-context constructor callback is an explicit counterexample outside that scope. Public SDK tables/DataFrames are already-created trusted Python objects; there is no claim about callbacks that occurred during their creation.

Native parser/decompression/pandas allocations can precede post-decode checks. The check script applies an outer 1 GiB/2 CPU/container limit; a parser crash, OOM, timeout, missing dependency or unfinished stream is operational **INCOMPLETE**, not `UNKNOWN_BUDGET` or a negative statistical result. Controlled logical cap exhaustion is `UNKNOWN_BUDGET` without a purported complete report. Malformed data is `INVALID_INPUT`; unsupported features are `UNSUPPORTED_INPUT`; materially invalid finite-range results are `NUMERICALLY_INDETERMINATE`. Only roundoff within 1e-14 of a proven endpoint may be snapped.

The complete report is built privately and its finite JSON/output size checked before return. Stdout is not atomic: consumers require the entire parsed document, `complete=true` and exit 0. I/O failure can truncate output and must not be accepted as completion.
