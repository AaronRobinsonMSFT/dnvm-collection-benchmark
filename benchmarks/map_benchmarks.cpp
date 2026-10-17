#include <benchmark/benchmark.h>

#include <cstdint>
#include <exception>
#include <string>

#include "dataset.h"
#include "maps.h"
#include "runtime_platform.h"

namespace
{
template <typename Map>
void lookup(benchmark::State& state, bool hit)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    const std::vector<std::uint32_t>& keys = hit ? data.hits : data.misses;
    for (auto _ : state)
    {
        for (std::uint32_t key : keys)
        {
            void* value = nullptr;
            bool found = map.find(key, value);
            benchmark::DoNotOptimize(found);
            benchmark::DoNotOptimize(value);
        }
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void insert(benchmark::State& state, bool reserved)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    for (auto _ : state)
    {
        Map map(reserved ? static_cast<std::uint32_t>(data.hits.size()) : 0);
        for (std::size_t i = 0; i < data.hits.size(); ++i)
        {
            bool added = map.insert(data.hits[i], &data.values[i]);
            benchmark::DoNotOptimize(added);
        }
        benchmark::DoNotOptimize(map.size());
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void duplicate(benchmark::State& state)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    for (auto _ : state)
    {
        for (std::size_t i = 0; i < data.hits.size(); ++i)
        {
            bool added = map.insert(data.hits[i], &data.values[i]);
            benchmark::DoNotOptimize(added);
        }
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void replace(benchmark::State& state)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    for (auto _ : state)
    {
        for (std::size_t i = 0; i < data.hits.size(); ++i)
        {
            void* value = &data.values[(i + 1) % data.values.size()];
            bool replaced = map.replace(data.hits[i], value);
            benchmark::DoNotOptimize(replaced);
        }
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void erase_insert(benchmark::State& state)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    for (auto _ : state)
    {
        for (std::size_t i = 0; i < data.hits.size(); ++i)
        {
            bool erased = map.erase(data.hits[i]);
            bool added = map.insert(data.hits[i], &data.values[i]);
            benchmark::DoNotOptimize(erased);
            benchmark::DoNotOptimize(added);
        }
    }
    state.SetItemsProcessed(state.iterations() * state.range(0) * 2);
}

template <typename Map>
void iterate(benchmark::State& state)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    for (auto _ : state)
    {
        std::uint64_t checksum = 0;
        auto visitor = [&checksum](std::uint32_t key, void* value) {
            checksum += key;
            benchmark::DoNotOptimize(value);
        };
        map.visit(visitor);
        benchmark::DoNotOptimize(checksum);
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void clear_refill(benchmark::State& state)
{
    dnvm::Dataset data(static_cast<std::uint32_t>(state.range(0)));
    Map map;
    dnvm::populate(map, data);
    for (auto _ : state)
    {
        map.clear();
        dnvm::populate(map, data);
        benchmark::DoNotOptimize(map.size());
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
}

template <typename Map>
void register_map(const std::string& name)
{
    for (std::int64_t size : {8, 64, 1024, 65536})
    {
        auto add = [&name, size](const std::string& scenario, auto function) {
            benchmark::RegisterBenchmark((name + "/" + scenario).c_str(),
                [function](benchmark::State& state) {
                    try
                    {
                        function(state);
                    }
                    catch (const std::exception& error)
                    {
                        state.SkipWithError(error.what());
                    }
                })->Arg(size)->Unit(benchmark::kNanosecond);
        };
        add("lookup_hit", [](benchmark::State& state) { lookup<Map>(state, true); });
        add("lookup_miss", [](benchmark::State& state) { lookup<Map>(state, false); });
        add("build_growing", [](benchmark::State& state) { insert<Map>(state, false); });
        add("build_reserved", [](benchmark::State& state) { insert<Map>(state, true); });
        add("duplicate", duplicate<Map>);
        add("replace", replace<Map>);
        add("erase_insert", erase_insert<Map>);
        add("iterate", iterate<Map>);
        add("clear_refill", clear_refill<Map>);
    }
}
}

int main(int argc, char** argv)
{
    benchmark::Initialize(&argc, argv);
    if (benchmark::ReportUnrecognizedArguments(argc, argv))
        return 1;
    benchmark::AddCustomContext("compiler", DNVM_COMPILER);
    benchmark::AddCustomContext("build_type", DNVM_BUILD_TYPE);
    benchmark::AddCustomContext("system", DNVM_SYSTEM);
    benchmark::AddCustomContext("architecture", DNVM_ARCH);
    benchmark::AddCustomContext("pointer_bits", std::to_string(sizeof(void*) * 8));
    benchmark::AddCustomContext("runtime_simd", dnvm_runtime_simd_path());
    benchmark::AddCustomContext("dataset_seed", std::to_string(dnvm::DatasetSeed));
    benchmark::AddCustomContext("runtime_revision", DNVM_runtime_REVISION);
    benchmark::AddCustomContext("abseil_revision", DNVM_abseil_REVISION);
    benchmark::AddCustomContext("benchmark_revision", DNVM_benchmark_REVISION);
    benchmark::AddCustomContext("sanitizers", DNVM_SANITIZERS);
#if defined(_LIBCPP_VERSION)
    benchmark::AddCustomContext("stdlib", "libc++ " + std::to_string(_LIBCPP_VERSION));
#elif defined(__GLIBCXX__)
    benchmark::AddCustomContext("stdlib", "libstdc++ " + std::to_string(__GLIBCXX__));
#elif defined(_MSVC_STL_VERSION)
    benchmark::AddCustomContext("stdlib", "MSVC STL " + std::to_string(_MSVC_STL_VERSION));
#endif
    register_map<dnvm::RuntimeMap>("runtime");
    register_map<dnvm::StdMap>("std");
    register_map<dnvm::AbseilMap>("abseil");
    benchmark::RunSpecifiedBenchmarks();
    benchmark::Shutdown();
}
