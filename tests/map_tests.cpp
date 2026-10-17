#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>

#include "dataset.h"
#include "maps.h"
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
        }
        allocation_failure();
        precomputed_hash();
        std::cout << "Passed: three map contracts, runtime allocation failures, precomputed hashes; SIMD="
                  << dnvm_runtime_simd_path() << "\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
