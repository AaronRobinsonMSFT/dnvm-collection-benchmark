#include <exception>
#include <iostream>
#include <string>

#include "memory_measurements.h"
#include "runtime_platform.h"

namespace
{
template <typename Map>
void print_cases(const char* implementation, bool& first)
{
    for (const char* scenario : dnvm::MemoryScenarios)
    {
        for (std::uint32_t size : dnvm::MemorySizes)
        {
            const dnvm::MemoryMeasurement result = dnvm::measure_memory<Map>(scenario, size);
            if (!first)
                std::cout << ",\n";
            first = false;
            std::cout << "{\"name\":\"" << implementation << "/" << scenario << "/" << size
                << "\",\"object_bytes\":" << result.object_bytes
                << ",\"before_heap_bytes\":" << result.before_heap_bytes
                << ",\"after_heap_bytes\":" << result.after_heap_bytes
                << ",\"after_destroy_heap_bytes\":" << result.after_destroy_heap_bytes
                << ",\"allocations\":" << result.traffic.allocations
                << ",\"deallocations\":" << result.traffic.deallocations
                << ",\"allocated_bytes\":" << result.traffic.allocated_bytes
                << ",\"freed_bytes\":" << result.traffic.freed_bytes
                << ",\"end_heap_bytes\":" << result.traffic.live_bytes
                << ",\"peak_heap_bytes\":" << result.traffic.peak_live_bytes << "}";
        }
    }
}

template <typename Map>
void print_lifecycles(const char* implementation, bool& first)
{
    for (bool reserved : {false, true})
    {
        for (std::uint32_t size : dnvm::MemorySizes)
        {
            const dnvm::LifecycleMeasurement result = dnvm::measure_lifecycle<Map>(reserved, size);
            if (!first)
                std::cout << ",\n";
            first = false;
            std::cout << "{\"implementation\":\"" << implementation
                << "\",\"mode\":\"" << (reserved ? "reserved" : "growing")
                << "\",\"size\":" << size << ",\"object_bytes\":" << result.object_bytes
                << ",\"empty_heap_bytes\":" << result.empty_heap_bytes
                << ",\"populated_heap_bytes\":" << result.populated_heap_bytes
                << ",\"cleared_heap_bytes\":" << result.cleared_heap_bytes
                << ",\"destroyed_heap_bytes\":" << result.destroyed_heap_bytes << "}";
        }
    }
}
}

int main(int argc, char**)
{
    if (argc != 1)
    {
        std::cerr << "map_memory takes no arguments and writes accounting JSON to stdout\n";
        return 1;
    }
    try
    {
        std::cout << "{\"schema_version\":1,\"context\":{"
            << "\"compiler\":\"" << DNVM_COMPILER
            << "\",\"build_type\":\"" << DNVM_BUILD_TYPE
            << "\",\"system\":\"" << DNVM_SYSTEM
            << "\",\"architecture\":\"" << DNVM_ARCH
            << "\",\"pointer_bits\":\"" << sizeof(void*) * 8
            << "\",\"runtime_simd\":\"" << dnvm_runtime_simd_path()
            << "\",\"dataset_seed\":\"" << dnvm::DatasetSeed
            << "\",\"runtime_revision\":\"" << DNVM_runtime_REVISION
            << "\",\"abseil_revision\":\"" << DNVM_abseil_REVISION
            << "\",\"benchmark_revision\":\"" << DNVM_benchmark_REVISION
            << "\",\"sanitizers\":\"" << DNVM_SANITIZERS
#if defined(_LIBCPP_VERSION)
            << "\",\"stdlib\":\"libc++ " << _LIBCPP_VERSION
#elif defined(__GLIBCXX__)
            << "\",\"stdlib\":\"libstdc++ " << __GLIBCXX__
#elif defined(_MSVC_STL_VERSION)
            << "\",\"stdlib\":\"MSVC STL " << _MSVC_STL_VERSION
#endif
            << "\"},\"cases\":[\n";
        bool first = true;
        print_cases<dnvm::CountedRuntimeMap>("runtime", first);
        print_cases<dnvm::CountedStdMap>("std", first);
        print_cases<dnvm::CountedAbseilMap>("abseil", first);
        std::cout << "],\"lifecycles\":[\n";
        first = true;
        print_lifecycles<dnvm::CountedRuntimeMap>("runtime", first);
        print_lifecycles<dnvm::CountedStdMap>("std", first);
        print_lifecycles<dnvm::CountedAbseilMap>("abseil", first);
        std::cout << "]}\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Memory accounting failed: " << error.what() << "\n";
        return 1;
    }
}
