#ifndef DNVM_MEMORY_ACCOUNTING_H
#define DNVM_MEMORY_ACCOUNTING_H

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <memory>
#include <unordered_map>

#include "maps.h"

namespace dnvm
{
struct AllocationStats
{
    std::uint64_t allocations = 0;
    std::uint64_t deallocations = 0;
    std::uint64_t allocated_bytes = 0;
    std::uint64_t freed_bytes = 0;
    std::uint64_t live_bytes = 0;
    std::uint64_t peak_live_bytes = 0;
};

class MemoryAccount
{
    struct NativeAllocator
    {
        dn_allocator_t base;
        MemoryAccount* account;
    };

public:
    MemoryAccount() : native_{{&native_vtable_}, this} {}
    MemoryAccount(const MemoryAccount&) = delete;
    MemoryAccount& operator=(const MemoryAccount&) = delete;

    void prepare(std::size_t bytes) const
    {
        const std::uint64_t limit = std::numeric_limits<std::uint64_t>::max();
        if (bytes > limit - stats_.live_bytes || bytes > limit - stats_.allocated_bytes ||
            stats_.allocations == limit)
            throw std::overflow_error("Memory-accounting counter overflow");
    }

    void record(void* pointer, std::size_t bytes)
    {
        prepare(bytes);
        if (!blocks_.emplace(pointer, bytes).second)
            fail("Allocation address already recorded");
        ++stats_.allocations;
        stats_.allocated_bytes += bytes;
        stats_.live_bytes += bytes;
        stats_.peak_live_bytes = std::max(stats_.peak_live_bytes, stats_.live_bytes);
    }

    void release(void* pointer) noexcept
    {
        const auto found = blocks_.find(pointer);
        if (found == blocks_.end())
            fail("Deallocation address was not recorded");
        const std::uint64_t limit = std::numeric_limits<std::uint64_t>::max();
        if (stats_.deallocations == limit || found->second > limit - stats_.freed_bytes ||
            found->second > stats_.live_bytes)
            fail("Deallocation accounting overflow");
        ++stats_.deallocations;
        stats_.freed_bytes += found->second;
        stats_.live_bytes -= found->second;
        blocks_.erase(found);
    }

    void begin_phase()
    {
        const std::uint64_t live = stats_.live_bytes;
        stats_ = {};
        stats_.live_bytes = live;
        stats_.peak_live_bytes = live;
    }

    AllocationStats stats() const { return stats_; }
    std::size_t live_allocations() const { return blocks_.size(); }
    dn_allocator_t* native_allocator() { return &native_.base; }

private:
    [[noreturn]] static void fail(const char* message) noexcept
    {
        std::fprintf(stderr, "Memory-accounting error: %s\n", message);
        std::abort();
    }

    static void* native_allocate(dn_allocator_t* allocator, std::size_t bytes) noexcept
    {
        MemoryAccount& account = *reinterpret_cast<NativeAllocator*>(allocator)->account;
        account.prepare(bytes);
        void* pointer = std::malloc(bytes);
        if (pointer == nullptr)
            return nullptr;
        try
        {
            account.record(pointer, bytes);
        }
        catch (const std::bad_alloc&)
        {
            std::free(pointer);
            return nullptr;
        }
        return pointer;
    }

    static void* native_reallocate(dn_allocator_t*, void*, std::size_t) noexcept
    {
        fail("The selected runtime map unexpectedly requested realloc");
    }

    static void native_free(dn_allocator_t* allocator, void* pointer) noexcept
    {
        if (pointer != nullptr)
        {
            reinterpret_cast<NativeAllocator*>(allocator)->account->release(pointer);
            std::free(pointer);
        }
    }

    inline static dn_allocator_vtable_t native_vtable_ = {
        native_allocate, native_reallocate, native_free
    };
    NativeAllocator native_;
    AllocationStats stats_;
    std::unordered_map<void*, std::size_t> blocks_;
};

// Counting allocators and untimed CoreCLR array tracking consult this scope.
// The ledger uses ordinary scalar allocation.
class MemoryScope
{
public:
    explicit MemoryScope(MemoryAccount& account)
    {
        if (active_ != nullptr)
            throw std::logic_error("Nested memory-accounting scopes are not supported");
        active_ = &account;
    }
    ~MemoryScope() { active_ = nullptr; }
    MemoryScope(const MemoryScope&) = delete;
    MemoryScope& operator=(const MemoryScope&) = delete;

    static MemoryAccount& current()
    {
        if (active_ == nullptr)
            throw std::logic_error("A counting allocator requires an active MemoryScope");
        return *active_;
    }
    static MemoryAccount* active() { return active_; }

private:
    inline static thread_local MemoryAccount* active_ = nullptr;
};

template <typename T>
class CountingAllocator
{
public:
    using value_type = T;
    using is_always_equal = std::true_type;

    CountingAllocator() = default;
    template <typename U>
    CountingAllocator(const CountingAllocator<U>&) {}

    T* allocate(std::size_t count)
    {
        if (count > std::numeric_limits<std::size_t>::max() / sizeof(T))
            throw std::bad_array_new_length();
        MemoryAccount& account = MemoryScope::current();
        account.prepare(count * sizeof(T));
        std::allocator<T> allocator;
        T* pointer = allocator.allocate(count);
        try
        {
            account.record(pointer, count * sizeof(T));
        }
        catch (const std::bad_alloc&)
        {
            allocator.deallocate(pointer, count);
            throw;
        }
        return pointer;
    }

    void deallocate(T* pointer, std::size_t count) noexcept
    {
        MemoryScope::current().release(pointer);
        std::allocator<T>{}.deallocate(pointer, count);
    }

    template <typename U>
    bool operator==(const CountingAllocator<U>&) const { return true; }
    template <typename U>
    bool operator!=(const CountingAllocator<U>&) const { return false; }
};

template <typename Map>
class CountedRuntimeMapAdapter : public Map
{
public:
    explicit CountedRuntimeMapAdapter(std::uint32_t capacity = 0)
        : Map(capacity, MemoryScope::current().native_allocator()) {}
};

template <typename Family>
struct CountedMaps
{
    using Key = typename Family::RuntimeMap::key_type;
    using StdContainer = typename Family::StdMap::container_type;
    using AbseilContainer = typename Family::AbseilMap::container_type;
    using Allocator = CountingAllocator<std::pair<const Key, void*>>;
    using RuntimeMap = CountedRuntimeMapAdapter<typename Family::RuntimeMap>;
    using StdMap = StandardMap<std::unordered_map<
        Key, void*, typename StdContainer::hasher, typename StdContainer::key_equal, Allocator>,
        Family::RuntimeMap::supports_replace>;
    using AbseilMap = StandardMap<absl::flat_hash_map<
        Key, void*, typename AbseilContainer::hasher, typename AbseilContainer::key_equal, Allocator>,
        Family::RuntimeMap::supports_replace>;

    static_assert(sizeof(RuntimeMap) == sizeof(typename Family::RuntimeMap),
                  "Tracking changed runtime object size");
    static_assert(sizeof(StdMap) == sizeof(typename Family::StdMap),
                  "Tracking changed std object size");
    static_assert(sizeof(AbseilMap) == sizeof(typename Family::AbseilMap),
                  "Tracking changed Abseil object size");
};

using CountedRuntimeMap = CountedMaps<U32Maps>::RuntimeMap;
using CountedStdMap = CountedMaps<U32Maps>::StdMap;
using CountedAbseilMap = CountedMaps<U32Maps>::AbseilMap;

template <typename Runtime>
struct CountedMaps<CoreClrFamily<Runtime>>
{
    using Key = typename Runtime::key_type;
    static constexpr bool is_set = Runtime::is_set;
    using RuntimeMap = Runtime;
    using PairAllocator = CountingAllocator<std::pair<const Key, void*>>;
    using KeyAllocator = CountingAllocator<Key>;
    using StdContainer = std::conditional_t<is_set,
        std::unordered_multiset<Key, std::hash<Key>, std::equal_to<Key>, KeyAllocator>,
        std::unordered_multimap<Key, void*, std::hash<Key>, std::equal_to<Key>, PairAllocator>>;
    using AbseilContainer = std::conditional_t<is_set,
        absl::btree_multiset<Key, std::less<Key>, KeyAllocator>,
        absl::btree_multimap<Key, void*, std::less<Key>, PairAllocator>>;
    using StdMap = StandardMultiMap<StdContainer, is_set, Runtime::supports_remove>;
    using AbseilMap = StandardMultiMap<AbseilContainer, is_set, Runtime::supports_remove>;
    static_assert(sizeof(StdMap) == sizeof(typename CoreClrFamily<Runtime>::StdMap),
                  "Tracking changed std object size");
    static_assert(sizeof(AbseilMap) == sizeof(typename CoreClrFamily<Runtime>::AbseilMap),
                  "Tracking changed Abseil object size");
};

static_assert(sizeof(CountedRuntimeMap) == sizeof(RuntimeMap), "Tracking changed runtime object size");
static_assert(sizeof(CountedStdMap) == sizeof(StdMap), "Tracking changed std object size");
static_assert(sizeof(CountedAbseilMap) == sizeof(AbseilMap), "Tracking changed Abseil object size");
}

#endif
