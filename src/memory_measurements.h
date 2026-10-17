#ifndef DNVM_MEMORY_MEASUREMENTS_H
#define DNVM_MEMORY_MEASUREMENTS_H

#include <string>

#include "dataset.h"
#include "memory_accounting.h"

namespace dnvm
{
inline constexpr const char* MemoryScenarios[] = {
    "lookup_hit", "lookup_miss", "build_growing", "build_reserved", "duplicate",
    "replace", "erase_insert", "iterate", "clear_refill"
};
inline constexpr std::uint32_t MemorySizes[] = {8, 64, 1024, 65536};
struct LifecycleMode
{
    const char* name;
    bool reserved;
};
inline constexpr LifecycleMode LifecycleModes[] = {{"growing", false}, {"reserved", true}};

inline void memory_check(bool condition, const char* message)
{
    if (!condition)
        throw std::runtime_error(message);
}

template <typename Map>
void memory_operation(Map& map, MapDataset<Map>& data, const std::string& scenario)
{
    if (scenario == "build_growing" || scenario == "build_reserved")
    {
        populate(map, data);
    }
    else if (scenario == "lookup_hit" || scenario == "lookup_miss")
    {
        const bool hit = scenario == "lookup_hit";
        const auto& keys = hit ? data.hit_queries : data.misses;
        for (std::size_t i = 0; i < keys.size(); ++i)
        {
            void* value = nullptr;
            memory_check(map.find(keys[i], value) == hit, "Memory lookup returned wrong presence");
            memory_check(!hit || value == &data.values[i], "Memory lookup returned wrong value");
        }
    }
    else if (scenario == "duplicate")
    {
        for (auto key : data.hit_queries)
            memory_check(!map.insert(key, nullptr), "Memory duplicate insertion was not rejected");
    }
    else if (scenario == "replace")
    {
        if constexpr (Map::supports_replace)
        {
            for (std::size_t i = 0; i < data.hits.size(); ++i)
                memory_check(map.replace(data.hits[i], &data.values[(i + 1) % data.values.size()]),
                             "Memory replacement failed");
        }
        else
            throw std::invalid_argument("Replacement is not supported for string comparisons");
    }
    else if (scenario == "erase_insert")
    {
        for (std::size_t i = 0; i < data.hits.size(); ++i)
        {
            memory_check(map.erase(data.hit_queries[i]), "Memory removal failed");
            memory_check(map.insert(data.hits[i], &data.values[i]), "Memory reinsertion failed");
        }
    }
    else if (scenario == "iterate")
    {
        std::uint64_t count = 0;
        std::uint64_t sum = 0;
        auto visitor = [&count, &sum](typename Map::key_type key, void*) {
            ++count;
            sum += key_checksum(key);
        };
        map.visit(visitor);
        std::uint64_t expected_sum = 0;
        for (auto key : data.hits)
            expected_sum += key_checksum(key);
        memory_check(count == data.hits.size(), "Memory traversal returned wrong count");
        memory_check(sum == expected_sum, "Memory traversal returned wrong keys");
    }
    else if (scenario == "clear_refill")
    {
        map.clear();
        populate(map, data);
    }
    else
    {
        throw std::invalid_argument("Unknown memory scenario: " + scenario);
    }
    memory_check(map.size() == data.hits.size(), "Memory operation changed the expected count");
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        void* value = nullptr;
        const std::size_t index = scenario == "replace" ? (i + 1) % data.values.size() : i;
        memory_check(map.find(data.hit_queries[i], value) && value == &data.values[index],
                     "Memory operation did not preserve expected entries");
    }
}

struct MemoryMeasurement
{
    bool includes_destruction;
    std::uint64_t object_bytes;
    std::uint64_t before_heap_bytes;
    std::uint64_t after_heap_bytes;
    std::uint64_t after_destroy_heap_bytes;
    AllocationStats traffic;
};

template <typename Map>
MemoryMeasurement measure_memory(const std::string& scenario, std::uint32_t size)
{
    MapDataset<Map> data(size);
    MemoryAccount account;
    MemoryScope scope(account);
    MemoryMeasurement result{};
    result.object_bytes = sizeof(Map);
    const bool build = scenario == "build_growing" || scenario == "build_reserved";
    result.includes_destruction = build;
    {
        Map map(scenario == "build_reserved" ? size : 0);
        if (!build)
        {
            populate(map, data);
            account.begin_phase();
            result.before_heap_bytes = account.stats().live_bytes;
        }
        memory_operation(map, data, scenario);
        result.after_heap_bytes = account.stats().live_bytes;
        result.traffic = account.stats();
    }
    if (build)
        result.traffic = account.stats();
    result.after_destroy_heap_bytes = account.stats().live_bytes;
    memory_check(account.live_allocations() == 0, "Memory scenario leaked collection allocations");
    return result;
}

struct LifecycleMeasurement
{
    std::uint64_t object_bytes;
    std::uint64_t empty_heap_bytes;
    std::uint64_t populated_heap_bytes;
    std::uint64_t cleared_heap_bytes;
    std::uint64_t destroyed_heap_bytes;
};

template <typename Map>
LifecycleMeasurement measure_lifecycle(bool reserved, std::uint32_t size)
{
    MapDataset<Map> data(size);
    MemoryAccount account;
    MemoryScope scope(account);
    LifecycleMeasurement result{};
    result.object_bytes = sizeof(Map);
    {
        Map map(reserved ? size : 0);
        result.empty_heap_bytes = account.stats().live_bytes;
        populate(map, data);
        result.populated_heap_bytes = account.stats().live_bytes;
        map.clear();
        memory_check(map.size() == 0, "Memory lifecycle clear did not empty collection");
        for (auto key : data.hit_queries)
        {
            void* value = nullptr;
            memory_check(!map.find(key, value), "Memory lifecycle found an entry after clear");
        }
        result.cleared_heap_bytes = account.stats().live_bytes;
    }
    result.destroyed_heap_bytes = account.stats().live_bytes;
    memory_check(account.live_allocations() == 0, "Memory lifecycle leaked collection allocations");
    return result;
}
}

#endif
