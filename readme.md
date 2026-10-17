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

Configure and build the data-collection targets from the repository root.
Functional correctness runs separately, once with Linux GCC and once with Linux
Clang; Windows and macOS only collect comparison data. No GitHub workflow is
implemented yet.

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build build/release --parallel 8
python3 scripts/run.py --build build/release --output results/macos-arm64
```

Neither measurement script runs CTest or the separate known-defect reproducer,
and neither requires `map_tests`. Use a new output directory for each run;
existing results are never overwritten. Defaults are five randomly
interleaved repetitions and at least 0.1 CPU seconds per sample. Keep the machine
otherwise idle; increasing `--min-time` and `--repetitions` can help characterize
noise, not eliminate environmental differences.
The runner explicitly selects the complete suite and retains raw samples, even
if Google Benchmark environment variables request a filter or aggregate-only output.
After CPU measurements, it runs the separate `map_memory` executable and merges
memory accounting into the same summary page. Instrumented timings are never
used or reported.

To add memory accounting to existing CPU results without rerunning performance:

```sh
python3 scripts/run_memory.py --build build/release --results results/macos-arm64
```

The memory command refuses to overwrite existing memory artifacts. Compiler,
configuration, dependency revisions, dataset, and scenario coverage must match
the CPU results. Hashes bind memory artifacts to the exact saved CPU inputs.

## Functional validation (Linux only)

Run the collection contract, memory-accounting, and reporting tests once per
Linux compiler, independently of CPU and memory collection. Sanitizers belong to
these validation builds, not measurement builds:

```sh
CC=gcc CXX=g++ cmake -S . -B build/test-gcc -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON -DDNVM_SANITIZERS=ON
cmake --build build/test-gcc --parallel 8 --target map_tests map_memory
ctest --test-dir build/test-gcc --output-on-failure --no-tests=error

CC=clang CXX=clang++ cmake -S . -B build/test-clang -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON -DDNVM_SANITIZERS=ON
cmake --build build/test-clang --parallel 8 --target map_tests map_memory
ctest --test-dir build/test-clang --output-on-failure --no-tests=error
```

For a separate duplicate-key regression check, run `build/test-gcc/map_tests --reproduce-duplicate`
(or the Clang equivalent).
The separate reproducer returns **1 when it detects the duplicate-after-deletion contract
violation**, 0 when it no longer reproduces, and 2 for an unrelated test failure.
It is deliberately not counted as a passing correctness test. Data-collection
reports mark functional tests and the reproducer as not run, retaining a warning
about the known defect. Do not use sanitized timings for performance comparisons.

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
   `benchmarks/map_memory.cpp` separately executes one checked batch of each
   scenario with collection-specific allocation accounting, plus growing/reserved
   lifecycle probes. It does not link Google Benchmark or collect timing.
4. **Record and report:** `scripts/run.py` records provenance and build
   configuration, invokes Google Benchmark, and calls `scripts/report.py`.
   The reporter rejects missing or failed samples, incomplete comparisons, dirty
   dependencies, revision mismatches, and non-Release/sanitized measurements.
   Workload-result and allocation-integrity checks remain part of data collection;
   they are not a second run of the functional suite.

Scenarios are `lookup_hit`, `lookup_miss`, `build_growing`, `build_reserved`,
`duplicate`, `replace`, `erase_insert`, `iterate`, and `clear_refill`. The last two
mutation scenarios intentionally measure combined operations, not isolated erase
or clear. Both build scenarios include construction and destruction; reservation
is included in `build_reserved`. Other scenarios reuse an existing table.
Traversal includes each implementation's public traversal API cost, including
runtime's callback boundary.

The baseline uses each implementation's default hasher and allocation policy.
It measures the collection as normally used, **not table layout in isolation**.
There is no common-hasher experiment, process-RSS measurement, concurrent
benchmark, or precomputed-hash performance comparison yet.

## Memory-accounting boundaries

The memory pass uses the original runtime allocator callbacks and stateless
counting allocators for STL/Abseil. An explicit `MemoryScope` selects one account
per thread; nested scopes and containers escaping that scope are unsupported.
The tracking ledger uses the ordinary allocator and is not itself counted.
Compile-time checks require counted and uninstrumented adapters to have identical
inline object sizes. Accounting code is not included in the CPU executable.

Footprint is inline object size plus live requested heap bytes. Runtime's heap
control structure and buffers are counted; the native adapter's inline pointer
handle is included too. Dataset buffers, borrowed pointees, tracking bookkeeping,
allocator metadata/size-class rounding, and other process memory are excluded.
This is requested storage, not RSS or an allocator's actual physical footprint.

Each memory scenario executes one batch, not the CPU run's calibrated iteration
count. Build scenarios include construction/reservation and destruction in
allocation traffic; other scenarios begin after population and end before
destruction. Footprint is sampled while the populated object is still alive.
Peak footprint includes pre-existing storage and overlapping old/new allocations
during growth. Separate lifecycle probes record empty, populated, cleared, and
destroyed states. Every probe checks that all tracked collection allocations are released.

## Reports

Each result directory contains raw `benchmarks.json`, `metadata.json`,
benchmark logs, CMake cache/compile commands, and `summary.md`, `summary.csv`, and
`summary.json`. Summaries normalize CPU time per key, retain raw repetitions, and
report median, coefficient of variation, and speedup relative to runtime.
Markdown groups results by scenario, with one row per size and implementations
side by side. It bolds the lowest median, marks CV above 5% with `[!]`, and keeps
exact CV values in a collapsible details table. JSON and CSV retain their
implementation-per-row format and full numeric precision.
Each scenario's performance table is immediately followed by its footprint
table when memory data is present. Expandable memory details show exact live/peak
bytes and allocation traffic; lifecycle tables show retention after clear.
Raw memory data and provenance are in `memory.json` and `memory-metadata.json`;
derived values are in `memory-summary.json`, `memory-summary.csv`, and
`memory-lifecycle.csv`. CPU JSON/CSV values are not changed by adding memory.
Ratios are within one run only; results from different machines are not pooled.
High variability is flagged, not turned into a performance gate.
New collection metadata records `functional_tests: "not_run"` and
`known_duplicate_defect_reproduced: null`; this does not claim correctness or
absence of the documented defect. Historical results retain their original test
logs and reproducer outcomes when reports are regenerated.
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
