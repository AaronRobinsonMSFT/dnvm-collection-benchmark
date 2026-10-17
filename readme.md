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

- `dn_simdhash_u32_ptr` (`uint32_t` keys, borrowed `void*` values), compared with `std::unordered_map` and `absl::flat_hash_map` using the same types.

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

Use a new output directory for each run.

## Collection and results

Build each platform, collect CPU and memory measurements, then generate reports.

**GitHub Actions:** The **Collections** workflow emits the `collection-results` artifact.

**Local:** Open `results/local/summary.md` in a Markdown viewer. CSV/JSON
summaries, raw data, and logs are in the same output directory.

To regenerate reports without collecting new data:

```sh
python3 scripts/report.py results/local
```

## Functional validation (Linux only)

Run once with GCC and once with Clang, with sanitizers on Clang.
Data collection does not run these tests.

```sh
CC=gcc CXX=g++ cmake -S . -B build/test-gcc -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON -DDNVM_SANITIZERS=OFF
cmake --build build/test-gcc --parallel 8 --target map_tests map_memory
ctest --test-dir build/test-gcc --output-on-failure --no-tests=error

CC=clang CXX=clang++ cmake -S . -B build/test-clang -DCMAKE_BUILD_TYPE=Debug \
    -DBUILD_TESTING=ON -DDNVM_SANITIZERS=ON
cmake --build build/test-clang --parallel 8 --target map_tests map_memory
ctest --test-dir build/test-clang --output-on-failure --no-tests=error
```
