# .NET native collection benchmarks

Measure whether the custom native collections in `dotnet/runtime` justify their
performance, complexity, and ergonomics compared with
supported C++ implementations. The runtime collection is the subject and source
of truth: its capabilities and assumptions determine tests and scenarios.

## License

The original code in this repository is MIT licensed; see [LICENSE](LICENSE).
Dependencies retain their own licenses:

- `dotnet/runtime` is MIT
- Abseil is Apache-2.0
- Google Benchmark is Apache-2.0

License notices remain in all submodules.

## Current scope

| Collection | Runtime | STL | Abseil |
|---|---|---|---|
| `u32` | `dn_simdhash_u32_ptr` | `std::unordered_map<uint32_t, void*>` | `absl::flat_hash_map<uint32_t, void*>` |
| `ptr` | `dn_simdhash_ptr_ptr` | `std::unordered_map<void*, void*>` | `absl::flat_hash_map<void*, void*>` |
| `ptrpair` | `dn_simdhash_ptrpair_ptr` | `std::unordered_map<dn_ptrpair_t, void*, PointerPairHash, PointerPairEqual>` | `absl::flat_hash_map<dn_ptrpair_t, void*, PointerPairHash, PointerPairEqual>` |
| `string` | `dn_simdhash_string_ptr` | `std::unordered_map<const char*, void*, CStringHash, CStringEqual>` | `absl::flat_hash_map<const char*, void*, CStringAbseilHash, CStringEqual>` |
| `shash` | `SHash<MapSHashTraits<uint32_t, void*>>` | `std::unordered_multimap<uint32_t, void*>` | `absl::btree_multimap<uint32_t, void*>` |
| `mapshash` | `MapSHash<uint32_t, void*>` | `std::unordered_multimap<uint32_t, void*>` | `absl::btree_multimap<uint32_t, void*>` |
| `setshash` | `SetSHash<uint32_t>` | `std::unordered_multiset<uint32_t>` | `absl::btree_multiset<uint32_t>` |
| `umap` | `dn_umap` | `std::unordered_map<void*, void*>` | `absl::flat_hash_map<void*, void*>` |
| `vector` | `dn_vector_ptr` | `std::vector<void*>` | `absl::InlinedVector<void*, 8>` |
| `sarray` | `SArray<void*>` | `std::vector<void*>` | `absl::InlinedVector<void*, 8>` |
| `inline_sarray` | `InlineSArray<void*, 8>` | `std::vector<void*>` | `absl::InlinedVector<void*, 8>` |
| `stack_sarray` | `StackSArray<void*>` | `std::vector<void*>` | `absl::InlinedVector<void*, 256 / sizeof(void*)>` |
| `arraylist` | `ArrayList` | `std::vector<void*>`, `std::deque<void*>` | `absl::InlinedVector<void*, 5>` |
| `slist` | `SList` (head-only, intrusive) | `std::forward_list<void*>` (preallocated nodes, `splice_after`) | - |

| Contract | Policy |
|---|---|
| Ownership | Borrowed keys and values; key storage excluded from collection memory |
| Datasets | Generated once per key type and size in each measurement process; shared across implementations/scenarios |
| Pointer pairs | Ordered; STL/Abseil share the Abseil hash combiner |
| Pointer datasets | Pointer-sized slots and seeded pair indices; pair hash-diversity checks run before population |
| Strings | NUL-terminated; content equality; STL/Abseil hash string views |
| String dataset | 0/32/64-byte prefixes + decimal IDs; separate equal-content hit buffers |
| CoreCLR integration | Pinned headers and implementation bodies; standalone types/contracts replace VM infrastructure/PCH includes |
| CoreCLR duplicates | Retained; duplicate-build measures population, duplicate insertion, destruction |
| CoreCLR sentinels | Key `0` reserved; `UINT32_MAX` also reserved for remove-enabled `shash` |
| CoreCLR removal | `shash` erases all matches; default `mapshash`/`setshash` have no removal |
| CoreCLR alternatives | STL hash containers vs. Abseil B-trees; B-trees have no reserve |
| CoreCLR memory | Array allocations tracked only in untimed memory/test binaries |
| dn_umap | Separate chaining; reserve requests nonzero buckets; native rehash allocation failure is not recoverable |
| Sequences | Borrowed pointer elements; native iteration, indexed reads/writes, builds and clear/refill where supported |
| dn_vector_ptr | Embedded native object; default initialization allocates 64 slots; reserved initialization requests the measured size; clear retains capacity |
| Inline arrays | Eight inline pointers; stack arrays use 256 bytes; native and Abseil growth/clear policies differ |
| ArrayList | Five inline pointers, then growing chunks; vector/deque/inline-vector alternatives; sizes 5/6 cover the inline boundary; no reserve or concurrency workloads |
| Reallocation accounting | Untimed native realloc preserves allocator behavior; successful replacements count as one allocation plus one deallocation, including in-place changes; allocator-internal transient storage excluded |
| SList | Preallocated nodes: contiguous native array vs. allocator-owned STL nodes; traversal and unlink/relink exclude allocation; memory includes nodes, excluding harness pool-management fields |

## Requirements and setup

- Git with submodule support and access to the three public dependency repositories.
- A C11 and C++17 compiler
- CMake 3.26+
- Python 3.9+

After cloning this repository, initialize its pinned dependencies:

```sh
git -c core.longpaths=true submodule update --init --depth 1
```

Add the options for your platform to the configure command:

| Platform | Configure options |
|---|---|
| Windows x64 / MSVC | `-A x64` |
| Windows x86 / MSVC | `-A Win32` |
| macOS arm64 / AppleClang | `-DCMAKE_OSX_ARCHITECTURES=arm64` |
| Linux x64 / GCC | `-DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++` |
| Linux x64 / Clang | `-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++` |

Build and collect CPU and memory results from the repository root:

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build build/release --config Release --parallel
python3 scripts/run.py --build build/release --output results/local
```

All collections run automatically. Use a new output directory for each run.
Dirty dependencies stop collection before timing. Dependency snapshots are saved
as `dependencies.json` and `memory-dependencies.json`; failures include changed
paths, relevant Git settings, file modes, and line endings. Git provenance queries
enable long paths, matching dependency checkout on Windows.

## Collection and results

Build each platform, collect CPU and memory measurements, then generate reports.

**GitHub Actions:** The **Collections** workflow emits the `collection-results` artifact.
Checkout snapshots are saved as `dependencies-<platform>.json` before building.
Open its root `summary.html` for all-platform graphs with one collection row
linking to each platform's charts. Separate platform reports remain available.

**Local:** Open `results/local/summary.md` in a Markdown viewer. CSV/JSON
summaries, raw data, and logs are in the same output directory.
The summary shows per-collection CPU and memory graphs. Open `summary.html` in a
browser for hover tooltips with exact values, CPU speedups, and variability.
The collection index links to the beginning of each collection's graphs.
Detailed measurements and lifecycle checkpoints remain in the CSV/JSON exports.
Keep the adjacent `graphs/` directory with the report when viewing or copying it.

To regenerate reports without collecting new data:

```sh
python3 scripts/report.py results/local
```

To regenerate a downloaded combined artifact:

```sh
python3 scripts/ci_report.py results/collections-RUN --commit COMMIT
```

Optional `executive-summary.json` input in the artifact directory adds run-specific
findings and action items. Its `commit` must match; `findings` entries contain
`finding` and `detail`, and `action_items` entries contain `action` and `detail`.
An optional `methodology` string describes the analysis. Conclusions are input
data, not collection-specific rules in the report generator.

## Functional validation (Linux only)

Runtime collections only; Clang with sanitizers.
The independent **Functional validation** workflow emits `validation-*` artifacts.
The **Collections** workflow does not run or wait for validation.

```sh
CC=clang CXX=clang++ cmake -S . -B build/test-clang -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON -DDNVM_SANITIZERS=ON
cmake --build build/test-clang --parallel 8 --target map_tests
ctest --test-dir build/test-clang --output-on-failure --no-tests=error
```
