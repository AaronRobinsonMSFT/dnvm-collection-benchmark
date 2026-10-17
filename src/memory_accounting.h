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

// Only counting allocators consult this scope; the ledger uses the normal allocator.
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

class CountedRuntimeMap : public RuntimeMap
{
public:
    explicit CountedRuntimeMap(std::uint32_t capacity = 0)
        : RuntimeMap(capacity, MemoryScope::current().native_allocator()) {}
};

using CountedStdMap = StandardMap<std::unordered_map<
    std::uint32_t, void*, std::hash<std::uint32_t>, std::equal_to<std::uint32_t>,
    CountingAllocator<std::pair<const std::uint32_t, void*>>>>;
using CountedAbseilMap = StandardMap<absl::flat_hash_map<
    std::uint32_t, void*, absl::flat_hash_map<std::uint32_t, void*>::hasher,
    absl::flat_hash_map<std::uint32_t, void*>::key_equal,
    CountingAllocator<std::pair<const std::uint32_t, void*>>>>;

static_assert(sizeof(CountedRuntimeMap) == sizeof(RuntimeMap), "Tracking changed runtime object size");
static_assert(sizeof(CountedStdMap) == sizeof(StdMap), "Tracking changed std object size");
static_assert(sizeof(CountedAbseilMap) == sizeof(AbseilMap), "Tracking changed Abseil object size");
}

#endif
