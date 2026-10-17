# .NET native collection benchmarks

Measure whether the custom native collections in `dotnet/runtime` justify their
performance, complexity, and ergonomics compared with
supported C++ implementations. The runtime collection is the subject and source
of truth: its capabilities and assumptions determine tests and scenarios.
This is not a general STL or Abseil evaluation.

## License

The original code in this repository is MIT licensed; see [LICENSE](LICENSE).
Dependencies retain their own licenses: runtime is MIT, Abseil is Apache-2.0,
and Google Benchmark is Apache-2.0. Their license notices remain in the submodules.
The MIT license does not relicense those dependencies.

## Current scope

One collection: `dn_simdhash_u32_ptr` (`uint32_t` keys, borrowed `void*` values),
compared with `std::unordered_map` and `absl::flat_hash_map` using the same types.
All instances are single-threaded; no adapter adds synchronization or owns
pointees. Comparisons use only the common contract, not iterator stability.

This first milestone validates the complete system locally on macOS arm64.
Windows x64/x86 and Linux GCC/Clang execution, GitHub workflows, and additional
collections come after review of this milestone. Portability is an implementation
goal, not a claim that those configurations have run.

## Requirements and setup

- Git with submodule support and access to the three public dependency repositories.
- A C11 and C++17 compiler; Apple command-line developer tools on macOS.
- CMake 3.26+ (the pinned runtime's requirement) and a build tool
  (the default macOS generator uses Make).
- Python 3.9+; the runner and reporter use only the standard library.

After cloning this repository, initialize its pinned dependencies:

```sh
git submodule update --init
```

Use the system CMake; there is no Python package or virtual-environment
requirement. On Apple Silicon with Homebrew, ensure `/opt/homebrew/bin` is on
`PATH` if `cmake` is not found.

Configure and build from the repository root:

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release --parallel 8
ctest --test-dir build/release --output-on-failure
python3 scripts/run.py --build build/release --output results/macos-arm64
```

The runner executes CTest again before measuring. Use a new output directory for
each run; existing results are never overwritten. Defaults are five randomly
interleaved repetitions and at least 0.1 CPU seconds per sample. Keep the machine
otherwise idle; increasing `--min-time` and `--repetitions` can help characterize
noise, not eliminate environmental differences.
The runner explicitly selects the complete suite and retains raw samples, even
if Google Benchmark environment variables request a filter or aggregate-only output.

For correctness validation with AddressSanitizer and UndefinedBehaviorSanitizer:

```sh
cmake -S . -B build/sanitize -DCMAKE_BUILD_TYPE=Debug -DDNVM_SANITIZERS=ON
cmake --build build/sanitize --parallel 8
ctest --test-dir build/sanitize --output-on-failure
build/sanitize/map_tests --reproduce-duplicate
```

The separate reproducer returns **1 when it detects the duplicate-after-deletion contract
violation**, 0 when it no longer reproduces, and 2 for an unrelated test failure.
It is deliberately not counted as a passing correctness test. The runner records
its outcome and carries a warning into the report. Do not use sanitized timings
for performance comparisons.

## How it works

1. **Build:** CMake compiles three original runtime C translation units directly
   from `external/runtime`. Abseil and Google Benchmark build in-tree through their
   supported CMake targets. All three dependencies are Git submodules pinned by
   commit; configuration does not download or update them.
2. **Adapt and verify:** `src/maps.h` contains thin adapters for insertion without
   overwrite, lookup, replacement, removal, reservation, clearing, and traversal.
   `tests/map_tests.cpp` checks common behavior and runtime-specific allocation
   failures and precomputed-hash APIs. Runtime signed error results are checked
   explicitly. A C probe rejects accidental scalar fallback and reports the
   compiled runtime SIMD path.
3. **Measure:** `benchmarks/map_benchmarks.cpp` registers three implementations,
   nine scenarios, and four sizes (8, 64, 1,024, 65,536 keys). `src/dataset.h`
   generates deterministic distinct keys, disjoint misses, and real borrowed
   pointers. Dataset generation and lookup-table construction are outside lookup
   timing.
4. **Record and report:** `scripts/run.py` runs tests, records provenance and build
   configuration, invokes Google Benchmark, and calls `scripts/report.py`.
   The reporter rejects missing or failed samples, incomplete comparisons, dirty
   dependencies, revision mismatches, and non-Release/sanitized measurements.

Scenarios are `lookup_hit`, `lookup_miss`, `build_growing`, `build_reserved`,
`duplicate`, `replace`, `erase_insert`, `iterate`, and `clear_refill`. The last two
mutation scenarios intentionally measure combined operations, not isolated erase
or clear. Both build scenarios include construction and destruction; reservation
is included in `build_reserved`. Other scenarios reuse an existing table.
Traversal includes each implementation's public traversal API cost, including
runtime's callback boundary.

The baseline uses each implementation's default hasher and allocation policy.
It measures the collection as normally used, **not table layout in isolation**.
There is no common-hasher experiment, allocation/RSS measurement, concurrent
benchmark, or precomputed-hash performance comparison yet.

Each result directory contains raw `benchmarks.json`, `metadata.json`, test and
benchmark logs, CMake cache/compile commands, and `summary.md`, `summary.csv`, and
`summary.json`. Summaries normalize CPU time per key, retain raw repetitions, and
report median, coefficient of variation, and speedup relative to runtime.
Ratios are within one run only; results from different machines are not pooled.
High variability is flagged, not turned into a performance gate.
Framework warnings (including unavailable CPU-frequency metadata or failed
affinity setup) are retained in the report; CPU frequency is not used to convert
the measured CPU time.

To regenerate a report without rerunning benchmarks:

```sh
python3 scripts/report.py results/macos-arm64
```

When adding a collection, establish its contract first, implement only
the necessary comparison adapters, then extend tests and scenarios. Keep build,
execution, and reporting explicit; avoid a generalized plugin framework.
