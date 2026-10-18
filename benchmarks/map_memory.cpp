#include <exception>
#include <iomanip>
#include <iostream>
#include <string>

#include "memory_measurements.h"
#include "sequence_measurements.h"
#include "runtime_platform.h"

namespace
{
void print_case(const std::string& name, const dnvm::MemoryMeasurement& result, bool& first)
{
    if (!first)
        std::cout << ",\n";
    first = false;
    std::cout << "{\"name\":\"" << name
        << "\",\"includes_destruction\":" << (result.includes_destruction ? "true" : "false")
        << ",\"object_bytes\":" << result.object_bytes
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

template <typename Map>
void print_cases(const char* implementation, bool& first)
{
    for (const char* scenario : dnvm::MemoryScenarios)
        if (dnvm::supports_scenario<Map>(scenario))
            for (std::uint32_t size : dnvm::MemorySizes)
                print_case(std::string(implementation) + "/" + scenario + "/" + std::to_string(size),
                           dnvm::measure_memory<Map>(scenario, size), first);
}

void print_lifecycle(const char* collection, const char* implementation, const char* mode,
                     std::uint32_t size, const dnvm::LifecycleMeasurement& result, bool& first)
{
    if (!first)
        std::cout << ",\n";
    first = false;
    std::cout << "{\"collection\":\"" << collection
        << "\",\"implementation\":\"" << implementation
        << "\",\"mode\":\"" << mode
        << "\",\"size\":" << size << ",\"object_bytes\":" << result.object_bytes
        << ",\"empty_heap_bytes\":" << result.empty_heap_bytes
        << ",\"populated_heap_bytes\":" << result.populated_heap_bytes
        << ",\"cleared_heap_bytes\":" << result.cleared_heap_bytes
        << ",\"destroyed_heap_bytes\":" << result.destroyed_heap_bytes << "}";
}

template <typename Map>
void print_lifecycles(const char* collection, const char* implementation, bool& first)
{
    for (const auto& mode : dnvm::LifecycleModes)
        for (std::uint32_t size : dnvm::MemorySizes)
            print_lifecycle(collection, implementation, mode.name, size,
                            dnvm::measure_lifecycle<Map>(mode.reserved, size), first);
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
        std::cout << "{\"schema_version\":2,\"context\":{"
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
            << "\"";
        dnvm::describe_maps([](const std::string& name, const char* description) {
            std::cout << "," << std::quoted(name) << ":" << std::quoted(description);
        });
        dnvm::describe_sequences([](const std::string& name, const char* description) {
            std::cout << "," << std::quoted(name) << ":" << std::quoted(description);
        });
        std::cout << "},\"lifecycle_modes\":[";
        bool first = true;
        for (const auto& mode : dnvm::LifecycleModes)
        {
            if (!first)
                std::cout << ",";
            first = false;
            std::cout << std::quoted(mode.name);
        }
        std::cout << "],\"lifecycle_modes_by_collection\":{";
        first = true;
        dnvm::for_each_sequence_family([&first](const char* collection, auto family, const char*) {
            if (!first)
                std::cout << ",";
            first = false;
            std::cout << std::quoted(collection) << ":[";
            bool first_mode = true;
            dnvm::for_each_sequence_lifecycle<typename decltype(family)::RuntimeSequence>(
                [&first_mode](const char* mode, bool) {
                    if (!first_mode)
                        std::cout << ",";
                    first_mode = false;
                    std::cout << std::quoted(mode);
                });
            std::cout << "]";
        });
        std::cout << "},\"cases\":[\n";
        first = true;
        dnvm::for_each_family([&first](const char* name, auto family, const char*) {
            using Maps = dnvm::CountedMaps<decltype(family)>;
            print_cases<typename Maps::RuntimeMap>((std::string(name) + "/runtime").c_str(), first);
            print_cases<typename Maps::StdMap>((std::string(name) + "/std").c_str(), first);
            print_cases<typename Maps::AbseilMap>((std::string(name) + "/abseil").c_str(), first);
        });
        dnvm::for_each_sequence_family([&first](const char* collection, auto family, const char*) {
            using Family = decltype(family);
            family.template implementations<dnvm::CountingAllocator>(
                [collection, &first](const char* implementation, auto tag) {
                    using Sequence = typename decltype(tag)::type;
                    dnvm::for_each_sequence_scenario<Sequence>([collection, implementation, &first](const char* scenario) {
                        dnvm::for_each_sequence_size<Family>([collection, implementation, scenario, &first](std::uint32_t size) {
                            print_case(std::string(collection) + "/" + implementation + "/" + scenario
                                       + "/" + std::to_string(size),
                                       dnvm::measure_sequence_memory<Sequence>(scenario, size), first);
                        });
                    });
                });
        });
        std::cout << "],\"lifecycles\":[\n";
        first = true;
        dnvm::for_each_family([&first](const char* name, auto family, const char*) {
            using Maps = dnvm::CountedMaps<decltype(family)>;
            print_lifecycles<typename Maps::RuntimeMap>(name, "runtime", first);
            print_lifecycles<typename Maps::StdMap>(name, "std", first);
            print_lifecycles<typename Maps::AbseilMap>(name, "abseil", first);
        });
        dnvm::for_each_sequence_family([&first](const char* collection, auto family, const char*) {
            using Family = decltype(family);
            family.template implementations<dnvm::CountingAllocator>(
                [collection, &first](const char* implementation, auto tag) {
                    using Sequence = typename decltype(tag)::type;
                    dnvm::for_each_sequence_lifecycle<Sequence>(
                        [collection, implementation, &first](const char* mode, bool reserved) {
                            dnvm::for_each_sequence_size<Family>([collection, implementation, mode, reserved, &first](std::uint32_t size) {
                                print_lifecycle(collection, implementation, mode, size,
                                    dnvm::measure_sequence_lifecycle<Sequence>(reserved, size), first);
                            });
                        });
                });
        });
        std::cout << "]}\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Memory accounting failed: " << error.what() << "\n";
        return 1;
    }
}
