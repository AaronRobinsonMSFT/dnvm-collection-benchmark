#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unordered_map>

#include "dataset.h"
#include "maps.h"
#include "memory_measurements.h"
#include "dn-simdhash-utils.h"
#include "runtime_platform.h"

#define REQUIRE(condition) \
    do { if (!(condition)) throw std::runtime_error( \
        std::string(__FILE__) + ":" + std::to_string(__LINE__) + ": " #condition); } while (false)

namespace
{
template <typename Map>
void contract(std::uint32_t count)
{
    Map map;
    int first = 1;
    int second = 2;
    void* value = &first;
    REQUIRE(map.size() == 0);
    REQUIRE(!map.find(0, value));
    REQUIRE(value == &first);
    REQUIRE(!map.erase(0));
    REQUIRE(!map.replace(0, &second));
    REQUIRE(map.insert(0, nullptr));
    REQUIRE(map.find(0, value));
    REQUIRE(value == nullptr);
    REQUIRE(!map.insert(0, &first));
    REQUIRE(map.find(0, value));
    REQUIRE(value == nullptr);
    REQUIRE(map.replace(0, &second));
    REQUIRE(map.find(0, value));
    REQUIRE(value == &second);
    REQUIRE(map.insert(std::numeric_limits<std::uint32_t>::max(), &first));
    REQUIRE(map.size() == 2);
    REQUIRE(map.erase(0));
    REQUIRE(!map.erase(0));
    REQUIRE(map.size() == 1);
    map.clear();
    REQUIRE(map.size() == 0);
    map.clear();
    REQUIRE(!map.find(std::numeric_limits<std::uint32_t>::max(), value));

    dnvm::Dataset data(count);
    dnvm::populate(map, data);
    map.reserve(count * 2);
    std::unordered_map<std::uint32_t, void*> expected;
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        REQUIRE(map.find(data.hits[i], value));
        REQUIRE(value == &data.values[i]);
        REQUIRE(!map.find(data.misses[i], value));
        REQUIRE(value == &data.values[i]);
        REQUIRE(!map.insert(data.hits[i], &first));
        REQUIRE(map.find(data.hits[i], value));
        REQUIRE(value == &data.values[i]);
        void* replacement = &data.values[(i + 1) % data.values.size()];
        REQUIRE(map.replace(data.hits[i], replacement));
        expected.emplace(data.hits[i], replacement);
    }
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        REQUIRE(map.erase(data.hits[i]));
        REQUIRE(map.insert(data.hits[i], expected.at(data.hits[i])));
    }
    REQUIRE(map.size() == data.hits.size());
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        REQUIRE(map.find(data.hits[i], value));
        REQUIRE(value == expected.at(data.hits[i]));
    }
    auto visitor = [&expected](std::uint32_t key, void* item) {
        REQUIRE(expected.at(key) == item);
        REQUIRE(expected.erase(key) == 1);
    };
    map.visit(visitor);
    REQUIRE(expected.empty());
    for (std::size_t i = 0; i < data.hits.size(); i += 2)
        REQUIRE(map.erase(data.hits[i]));
    REQUIRE(map.size() == data.hits.size() / 2);
    for (std::size_t i = 0; i < data.hits.size(); ++i)
        REQUIRE(map.find(data.hits[i], value) == (i % 2 != 0));
    map.clear();
    REQUIRE(map.size() == 0);
    for (std::uint32_t key : data.hits)
        REQUIRE(!map.find(key, value));
    dnvm::populate(map, data);
    REQUIRE(map.size() == data.hits.size());
}

template <typename Map>
void key_contract(std::uint32_t count)
{
    using Key = typename Map::key_type;
    dnvm::MapDataset<Map> data(count);
    Map map;
    int sentinel = 1;
    void* value = &sentinel;
    REQUIRE(!map.find(data.hit_queries.front(), value));
    REQUIRE(value == &sentinel);
    REQUIRE(!map.erase(data.misses.front()));
    dnvm::populate(map, data);
    map.reserve(count * 2);
    std::unordered_map<Key, void*, typename dnvm::KeyTraits<Key>::Hash,
                       typename dnvm::KeyTraits<Key>::Equal> expected;
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        REQUIRE(map.find(data.hit_queries[i], value));
        REQUIRE(value == &data.values[i]);
        REQUIRE(!map.find(data.misses[i], value));
        REQUIRE(value == &data.values[i]);
        REQUIRE(!map.insert(data.hit_queries[i], &sentinel));
        REQUIRE(map.find(data.hit_queries[i], value));
        REQUIRE(value == &data.values[i]);
        if constexpr (Map::supports_replace)
        {
            REQUIRE(!map.replace(data.misses[i], &sentinel));
            REQUIRE(map.replace(data.hit_queries[i], nullptr));
            REQUIRE(map.find(data.hit_queries[i], value));
            REQUIRE(value == nullptr);
            REQUIRE(map.replace(data.hit_queries[i], &data.values[i]));
        }
        expected.emplace(data.hits[i], &data.values[i]);
        REQUIRE(map.erase(data.hit_queries[i]));
        REQUIRE(!map.erase(data.hit_queries[i]));
        REQUIRE(map.insert(data.hits[i], &data.values[i]));
    }
    REQUIRE(map.size() == count);
    auto visitor = [&expected](Key key, void* item) {
        REQUIRE(expected.at(key) == item);
        REQUIRE(expected.erase(key) == 1);
    };
    map.visit(visitor);
    REQUIRE(expected.empty());
    for (std::size_t i = 0; i < data.hits.size(); i += 2)
        REQUIRE(map.erase(data.hit_queries[i]));
    for (std::size_t i = 0; i < data.hits.size(); ++i)
        REQUIRE(map.find(data.hit_queries[i], value) == (i % 2 != 0));
    map.clear();
    map.clear();
    REQUIRE(map.size() == 0);
    for (auto key : data.hit_queries)
        REQUIRE(!map.find(key, value));
    dnvm::populate(map, data);
    REQUIRE(map.size() == count);
}

template <typename Map>
void key_edges()
{
    using Key = typename Map::key_type;
    Map map;
    int first = 1, second = 2;
    std::vector<Key> keys;
    if constexpr (std::is_same_v<Key, void*>)
        keys = {nullptr, &first, &second};
    else if constexpr (std::is_same_v<Key, dn_ptrpair_t>)
        keys = {{nullptr, nullptr}, {&first, nullptr}, {nullptr, &first},
                {&first, &second}, {&second, &first}};
    else
        keys = {"", "key", "key-prefix", "\xc4\xb0", "\xf0\x9f\x98\x80"};
    for (auto key : keys)
    {
        REQUIRE(map.insert(key, nullptr));
        void* value = &first;
        REQUIRE(map.find(key, value));
        REQUIRE(value == nullptr);
    }
    REQUIRE(map.size() == keys.size());
    if constexpr (std::is_same_v<Key, const char*>)
    {
        char same_content[] = "key";
        void* value = &first;
        REQUIRE(map.find(same_content, value));
        REQUIRE(value == nullptr);
        REQUIRE(!map.insert(same_content, &first));
        REQUIRE(map.erase(same_content));
        REQUIRE(!map.find("key", value));
    }
}

template <typename Family>
void family_contract(std::uint32_t count)
{
    key_contract<typename Family::RuntimeMap>(count);
    key_contract<typename Family::StdMap>(count);
    key_contract<typename Family::AbseilMap>(count);
    dnvm::MemoryAccount account;
    {
        dnvm::MemoryScope scope(account);
        using Counted = dnvm::CountedMaps<Family>;
        key_contract<typename Counted::RuntimeMap>(count);
        key_contract<typename Counted::StdMap>(count);
        key_contract<typename Counted::AbseilMap>(count);
    }
    REQUIRE(account.live_allocations() == 0);
    REQUIRE(account.stats().live_bytes == 0);
    key_edges<typename Family::RuntimeMap>();
    key_edges<typename Family::StdMap>();
    key_edges<typename Family::AbseilMap>();
}

struct FailingAllocator
{
    dn_allocator_t allocator;
    std::size_t calls = 0;
    std::size_t fail_at = 0;
    std::size_t live = 0;

    static void* allocate(dn_allocator_t* allocator, std::size_t size)
    {
        FailingAllocator* self = reinterpret_cast<FailingAllocator*>(allocator);
        if (++self->calls == self->fail_at)
            return nullptr;
        void* result = std::malloc(size);
        if (result != nullptr)
            ++self->live;
        return result;
    }
    static void* reallocate(dn_allocator_t*, void*, std::size_t)
    {
        std::abort(); // The selected implementation never calls realloc.
    }
    static void release(dn_allocator_t* allocator, void* pointer)
    {
        FailingAllocator* self = reinterpret_cast<FailingAllocator*>(allocator);
        if (pointer != nullptr)
        {
            --self->live;
            std::free(pointer);
        }
    }
    FailingAllocator()
    {
        static dn_allocator_vtable_t vtable = {allocate, reallocate, release};
        allocator._vtable = &vtable;
    }
};

template <typename Map>
void key_allocation_failure()
{
    dnvm::MapDataset<Map> data(4096);
    for (std::size_t failure = 1; failure <= 3; ++failure)
    {
        FailingAllocator allocator;
        allocator.fail_at = failure;
        bool rejected = false;
        try
        {
            Map map(0, &allocator.allocator);
        }
        catch (const std::bad_alloc&)
        {
            rejected = true;
        }
        REQUIRE(rejected);
        REQUIRE(allocator.live == 0);
    }
    for (std::size_t failure = 1; failure <= 2; ++failure)
    {
        FailingAllocator allocator;
        {
            Map map(0, &allocator.allocator);
            REQUIRE(map.insert(data.hits.front(), nullptr));
            allocator.fail_at = allocator.calls + failure;
            bool rejected = false;
            try
            {
                map.reserve(4096);
            }
            catch (const std::bad_alloc&)
            {
                rejected = true;
            }
            REQUIRE(rejected);
            void* value = &allocator;
            REQUIRE(map.find(data.hit_queries.front(), value));
            REQUIRE(value == nullptr);
            REQUIRE(map.size() == 1);
        }
        REQUIRE(allocator.live == 0);
    }
    for (std::size_t failure = 1; failure <= 2; ++failure)
    {
        FailingAllocator allocator;
        {
            Map map(0, &allocator.allocator);
            const std::size_t original_calls = allocator.calls;
            allocator.fail_at = original_calls + failure;
            std::size_t inserted = 0;
            bool rejected = false;
            try
            {
                while (inserted < data.hits.size())
                {
                    REQUIRE(map.insert(data.hits[inserted], &data.values[inserted]));
                    ++inserted;
                }
            }
            catch (const std::bad_alloc&)
            {
                rejected = true;
            }
            REQUIRE(rejected);
            REQUIRE(map.size() == inserted);
            for (std::size_t i = 0; i < inserted; ++i)
            {
                void* value = nullptr;
                REQUIRE(map.find(data.hit_queries[i], value));
                REQUIRE(value == &data.values[i]);
            }
        }
        REQUIRE(allocator.live == 0);
    }
}

void allocation_failure()
{
    for (std::size_t failure = 1; failure <= 3; ++failure)
    {
        FailingAllocator allocator;
        allocator.fail_at = failure;
        REQUIRE(dn_simdhash_u32_ptr_new(0, &allocator.allocator) == nullptr);
        REQUIRE(allocator.live == 0);
    }
    for (std::size_t failure = 1; failure <= 2; ++failure)
    {
        FailingAllocator allocator;
        dn_simdhash_u32_ptr_t* map = dn_simdhash_u32_ptr_new(0, &allocator.allocator);
        REQUIRE(map != nullptr);
        int item = 42;
        REQUIRE(dn_simdhash_u32_ptr_try_add(map, 1, &item) == DN_SIMDHASH_ADD_INSERTED);
        allocator.fail_at = allocator.calls + failure;
        REQUIRE(!dn_simdhash_ensure_capacity(map, 4096));
        void* value = nullptr;
        REQUIRE(dn_simdhash_u32_ptr_try_get_value(map, 1, &value));
        REQUIRE(value == &item);
        REQUIRE(dn_simdhash_count(map) == 1);
        dn_simdhash_free(map);
        REQUIRE(allocator.live == 0);
    }
    for (std::size_t failure = 1; failure <= 2; ++failure)
    {
        FailingAllocator allocator;
        dn_simdhash_u32_ptr_t* map = dn_simdhash_u32_ptr_new(0, &allocator.allocator);
        REQUIRE(map != nullptr);
        const std::uint32_t count = map->grow_at_count;
        for (std::uint32_t key = 0; key < count; ++key)
            REQUIRE(dn_simdhash_u32_ptr_try_add(map, key, nullptr) == DN_SIMDHASH_ADD_INSERTED);
        allocator.fail_at = allocator.calls + failure;
        REQUIRE(dn_simdhash_u32_ptr_try_add(map, count, nullptr) == DN_SIMDHASH_OUT_OF_MEMORY);
        REQUIRE(dn_simdhash_count(map) == count);
        for (std::uint32_t key = 0; key < count; ++key)
            REQUIRE(dn_simdhash_u32_ptr_try_get_value(map, key, nullptr));
        dn_simdhash_free(map);
        REQUIRE(allocator.live == 0);
    }
}

void precomputed_hash()
{
    dn_simdhash_u32_ptr_t* map = dn_simdhash_u32_ptr_new(0, nullptr);
    REQUIRE(map != nullptr);
    int first = 1;
    int second = 2;
    const std::uint32_t hash = murmur3_fmix32(42);
    REQUIRE(dn_simdhash_u32_ptr_try_add_with_hash(map, 42, hash, &first) == DN_SIMDHASH_ADD_INSERTED);
    REQUIRE(dn_simdhash_ensure_capacity(map, 4096));
    void* value = nullptr;
    REQUIRE(dn_simdhash_u32_ptr_try_get_value_with_hash(map, 42, hash, &value));
    REQUIRE(value == &first);
    REQUIRE(dn_simdhash_u32_ptr_try_replace_value_with_hash(map, 42, hash, &second));
    REQUIRE(dn_simdhash_u32_ptr_try_get_value(map, 42, &value));
    REQUIRE(value == &second);
    REQUIRE(dn_simdhash_u32_ptr_try_remove_with_hash(map, 42, hash));
    REQUIRE(!dn_simdhash_u32_ptr_try_remove_with_hash(map, 42, hash));
    dn_simdhash_free(map);
}

int reproduce_duplicate()
{
    dn_simdhash_u32_ptr_t* map = dn_simdhash_u32_ptr_new(20, nullptr);
    REQUIRE(map != nullptr);
    const std::uint32_t bucket_count = map->buffers.buckets_length;
    std::vector<std::uint32_t> keys;
    for (std::uint32_t key = 0; keys.size() < 13; ++key)
    {
        if (murmur3_fmix32(key) % bucket_count == 0)
            keys.push_back(key);
    }
    for (std::uint32_t key : keys)
        REQUIRE(dn_simdhash_u32_ptr_try_add(map, key, nullptr) == DN_SIMDHASH_ADD_INSERTED);
    REQUIRE(dn_simdhash_u32_ptr_try_remove(map, keys.front()));
    const std::uint32_t before = dn_simdhash_count(map);
    const dn_simdhash_add_result result = dn_simdhash_u32_ptr_try_add(map, keys.back(), nullptr);
    const std::uint32_t after = dn_simdhash_count(map);
    dn_simdhash_free(map);
    if (result != DN_SIMDHASH_ADD_FAILED || before != after)
    {
        std::cerr << "KNOWN CONTRACT DEFECT: duplicate overflow key inserted after deletion; count "
                  << before << " -> " << after << "\n";
        return 1;
    }
    std::cout << "Duplicate-key regression no longer reproduces.\n";
    return 0;
}

void memory_accounting()
{
    dnvm::MemoryAccount account;
    dnvm::MemoryScope scope(account);
    dnvm::CountingAllocator<std::uint64_t> allocator;
    std::uint64_t* first = allocator.allocate(8);
    std::uint64_t* second = allocator.allocate(4);
    REQUIRE(account.stats().allocations == 2);
    REQUIRE(account.stats().allocated_bytes == 96);
    REQUIRE(account.stats().live_bytes == 96);
    REQUIRE(account.stats().peak_live_bytes == 96);
    account.begin_phase();
    allocator.deallocate(first, 8);
    first = allocator.allocate(2);
    REQUIRE(account.stats().allocations == 1);
    REQUIRE(account.stats().deallocations == 1);
    REQUIRE(account.stats().allocated_bytes == 16);
    REQUIRE(account.stats().freed_bytes == 64);
    REQUIRE(account.stats().live_bytes == 48);
    REQUIRE(account.stats().peak_live_bytes == 96);
    allocator.deallocate(first, 2);
    allocator.deallocate(second, 4);
    REQUIRE(account.live_allocations() == 0);
    REQUIRE(account.stats().live_bytes == 0);
    struct alignas(64) AlignedValue { char bytes[64]; };
    dnvm::CountingAllocator<AlignedValue> aligned_allocator;
    AlignedValue* aligned = aligned_allocator.allocate(2);
    REQUIRE(reinterpret_cast<std::uintptr_t>(aligned) % 64 == 0);
    aligned_allocator.deallocate(aligned, 2);
    REQUIRE(account.live_allocations() == 0);
}

template <typename Map>
void counted_contract(std::uint32_t count)
{
    dnvm::MemoryAccount account;
    dnvm::MemoryScope scope(account);
    contract<Map>(count);
    REQUIRE(account.live_allocations() == 0);
    REQUIRE(account.stats().live_bytes == 0);
}

void memory_phases()
{
    const dnvm::MemoryMeasurement lookup =
        dnvm::measure_memory<dnvm::CountedRuntimeMap>("lookup_hit", 64);
    REQUIRE(lookup.traffic.allocations == 0);
    REQUIRE(lookup.traffic.allocated_bytes == 0);
    REQUIRE(lookup.before_heap_bytes == lookup.after_heap_bytes);
    REQUIRE(lookup.after_destroy_heap_bytes == 0);
    const dnvm::MemoryMeasurement build =
        dnvm::measure_memory<dnvm::CountedRuntimeMap>("build_growing", 64);
    REQUIRE(build.before_heap_bytes == 0);
    REQUIRE(build.traffic.allocations > 0);
    REQUIRE(build.traffic.allocations == build.traffic.deallocations);
    REQUIRE(build.traffic.allocated_bytes == build.traffic.freed_bytes);
    REQUIRE(build.traffic.live_bytes == 0);
    REQUIRE(build.traffic.peak_live_bytes >= build.after_heap_bytes);
    const dnvm::LifecycleMeasurement lifecycle =
        dnvm::measure_lifecycle<dnvm::CountedRuntimeMap>(false, 64);
    REQUIRE(lifecycle.populated_heap_bytes == lifecycle.cleared_heap_bytes);
    REQUIRE(lifecycle.destroyed_heap_bytes == 0);
}

void memory_scope_checks()
{
    dnvm::CountingAllocator<std::uint64_t> allocator;
    bool rejected = false;
    try
    {
        allocator.allocate(1);
    }
    catch (const std::logic_error&)
    {
        rejected = true;
    }
    REQUIRE(rejected);
    dnvm::MemoryAccount first;
    dnvm::MemoryAccount second;
    dnvm::MemoryScope scope(first);
    rejected = false;
    try
    {
        dnvm::MemoryScope nested(second);
    }
    catch (const std::logic_error&)
    {
        rejected = true;
    }
    REQUIRE(rejected);
    rejected = false;
    try
    {
        allocator.allocate(std::numeric_limits<std::size_t>::max() / sizeof(std::uint64_t) + 1);
    }
    catch (const std::bad_array_new_length&)
    {
        rejected = true;
    }
    REQUIRE(rejected);
    REQUIRE(first.stats().allocations == 0);
}

void native_memory_sizes()
{
    dnvm::MemoryAccount account;
    dn_simdhash_u32_ptr_t* map = dn_simdhash_u32_ptr_new(0, account.native_allocator());
    REQUIRE(map != nullptr);
    const std::uint64_t expected =
        sizeof(dn_simdhash_t) + map->meta->data_size +
        map->buffers.buckets_length * map->meta->bucket_size_bytes + DN_SIMDHASH_VECTOR_WIDTH +
        map->buffers.values_length * map->meta->value_size;
    REQUIRE(account.stats().allocations == 3);
    REQUIRE(account.stats().live_bytes == expected);
    dn_simdhash_free(map);
    REQUIRE(account.stats().allocations == account.stats().deallocations);
    REQUIRE(account.stats().allocated_bytes == account.stats().freed_bytes);
    REQUIRE(account.live_allocations() == 0);
}
}

int main(int argc, char** argv)
{
    try
    {
        if (argc == 2 && std::string(argv[1]) == "--reproduce-duplicate")
            return reproduce_duplicate();
        REQUIRE(argc == 1);
        for (std::uint32_t count : {8, 64, 1024, 65536})
        {
            contract<dnvm::RuntimeMap>(count);
            contract<dnvm::StdMap>(count);
            contract<dnvm::AbseilMap>(count);
            counted_contract<dnvm::CountedRuntimeMap>(count);
            counted_contract<dnvm::CountedStdMap>(count);
            counted_contract<dnvm::CountedAbseilMap>(count);
            family_contract<dnvm::PointerMaps>(count);
            family_contract<dnvm::PointerPairMaps>(count);
            family_contract<dnvm::StringMaps>(count);
        }
        memory_accounting();
        memory_phases();
        memory_scope_checks();
        native_memory_sizes();
        allocation_failure();
        key_allocation_failure<dnvm::RuntimePointerMap>();
        key_allocation_failure<dnvm::RuntimePointerPairMap>();
        key_allocation_failure<dnvm::RuntimeStringMap>();
        precomputed_hash();
        std::cout << "Passed: four key families, runtime allocation failures, precomputed hashes; SIMD="
                  << dnvm_runtime_simd_path() << "\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
