#ifndef DNVM_MAPS_H
#define DNVM_MAPS_H

#include <cstdint>
#include <new>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>

#include "absl/container/flat_hash_map.h"
#include "key_types.h"

namespace dnvm
{
template <typename Key, auto New, auto Add, auto Find, auto Replace, auto Remove, auto Foreach>
class RuntimeMapAdapter
{
public:
    using key_type = Key;
    static constexpr bool supports_replace = Replace != nullptr;

    explicit RuntimeMapAdapter(std::uint32_t capacity = 0, dn_allocator_t* allocator = nullptr)
        : map_(New(capacity, allocator))
    {
        if (map_ == nullptr)
            throw std::bad_alloc();
    }

    ~RuntimeMapAdapter() { dn_simdhash_free(map_); }
    RuntimeMapAdapter(const RuntimeMapAdapter&) = delete;
    RuntimeMapAdapter& operator=(const RuntimeMapAdapter&) = delete;

    bool insert(Key key, void* value)
    {
        const auto result = Add(map_, key, value);
        // The string wrapper narrows the native enum (including -1) to uint8_t.
        if (result == static_cast<decltype(result)>(DN_SIMDHASH_OUT_OF_MEMORY))
            throw std::bad_alloc();
        switch (result)
        {
            case DN_SIMDHASH_ADD_INSERTED: return true;
            case DN_SIMDHASH_ADD_FAILED: return false;
            default: throw std::runtime_error("runtime map insertion failed internally");
        }
    }

    bool find(Key key, void*& value) const
    {
        return Find(map_, key, &value) != 0;
    }

    bool replace(Key key, void* value)
    {
        static_assert(supports_replace, "The runtime string wrapper has no replace operation");
        return Replace(map_, key, value) != 0;
    }

    bool erase(Key key) { return Remove(map_, key) != 0; }
    std::uint32_t size() const { return dn_simdhash_count(map_); }
    void clear() { dn_simdhash_clear(map_); }

    void reserve(std::uint32_t count)
    {
        if (!dn_simdhash_ensure_capacity(map_, count))
            throw std::bad_alloc();
    }

    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        Foreach(map_, [](Key key, void* value, void* context) {
            (*static_cast<Visitor*>(context))(key, value);
        }, &visitor);
    }

private:
    dn_simdhash_t* map_;
};

using RuntimeMap = RuntimeMapAdapter<std::uint32_t,
    dn_simdhash_u32_ptr_new, dn_simdhash_u32_ptr_try_add, dn_simdhash_u32_ptr_try_get_value,
    dn_simdhash_u32_ptr_try_replace_value, dn_simdhash_u32_ptr_try_remove, dn_simdhash_u32_ptr_foreach>;
using RuntimePointerMap = RuntimeMapAdapter<void*,
    dn_simdhash_ptr_ptr_new, dn_simdhash_ptr_ptr_try_add, dn_simdhash_ptr_ptr_try_get_value,
    dn_simdhash_ptr_ptr_try_replace_value, dn_simdhash_ptr_ptr_try_remove, dn_simdhash_ptr_ptr_foreach>;
using RuntimePointerPairMap = RuntimeMapAdapter<dn_ptrpair_t,
    dn_simdhash_ptrpair_ptr_new, dn_simdhash_ptrpair_ptr_try_add, dn_simdhash_ptrpair_ptr_try_get_value,
    dn_simdhash_ptrpair_ptr_try_replace_value, dn_simdhash_ptrpair_ptr_try_remove,
    dn_simdhash_ptrpair_ptr_foreach>;
using RuntimeStringMap = RuntimeMapAdapter<const char*,
    dn_simdhash_string_ptr_new, dn_simdhash_string_ptr_try_add, dn_simdhash_string_ptr_try_get_value,
    nullptr, dn_simdhash_string_ptr_try_remove, dn_simdhash_string_ptr_foreach>;

template <typename Map, bool SupportsReplace = true>
class StandardMap
{
public:
    using key_type = typename Map::key_type;
    using container_type = Map;
    static constexpr bool supports_replace = SupportsReplace;

    explicit StandardMap(std::uint32_t capacity = 0)
    {
        if (capacity != 0)
            map_.reserve(capacity);
    }

    bool insert(key_type key, void* value) { return map_.emplace(key, value).second; }
    bool find(key_type key, void*& value) const
    {
        const typename Map::const_iterator found = map_.find(key);
        if (found == map_.end())
            return false;
        value = found->second;
        return true;
    }

    bool replace(key_type key, void* value)
    {
        static_assert(supports_replace, "Replacement is not compared for this family");
        const typename Map::iterator found = map_.find(key);
        if (found == map_.end())
            return false;
        found->second = value;
        return true;
    }

    bool erase(key_type key) { return map_.erase(key) != 0; }
    std::size_t size() const { return map_.size(); }
    void clear() { map_.clear(); }
    void reserve(std::uint32_t count) { map_.reserve(count); }

    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        for (const typename Map::value_type& item : map_)
            visitor(item.first, item.second);
    }

private:
    Map map_;
};

using StdMap = StandardMap<std::unordered_map<std::uint32_t, void*>>;
using AbseilMap = StandardMap<absl::flat_hash_map<std::uint32_t, void*>>;

template <typename Runtime, typename Key = typename Runtime::key_type>
struct MapFamily
{
    using RuntimeMap = Runtime;
    using StdMap = StandardMap<std::unordered_map<
        Key, void*, typename KeyTraits<Key>::Hash, typename KeyTraits<Key>::Equal>,
        Runtime::supports_replace>;
    using AbseilMap = StandardMap<absl::flat_hash_map<
        Key, void*, typename KeyTraits<Key>::AbseilHash, typename KeyTraits<Key>::Equal>,
        Runtime::supports_replace>;
};

using U32Maps = MapFamily<RuntimeMap>;
using PointerMaps = MapFamily<RuntimePointerMap>;
using PointerPairMaps = MapFamily<RuntimePointerPairMap>;
using StringMaps = MapFamily<RuntimeStringMap>;

template <typename Visitor>
void for_each_family(Visitor visitor)
{
    visitor("u32", U32Maps{}, "dn_simdhash_u32_ptr; uint32_t keys; borrowed pointer values.");
    visitor("ptr", PointerMaps{}, "dn_simdhash_ptr_ptr; borrowed pointer keys and values.");
    visitor("ptrpair", PointerPairMaps{},
        "dn_simdhash_ptrpair_ptr; ordered borrowed pointer pairs; STL/Abseil share the Abseil hash combiner.");
    visitor("string", StringMaps{},
        "dn_simdhash_string_ptr; borrowed NUL-terminated keys; content equality; STL/Abseil hash string views. "
        "0/32/64-byte prefixes plus decimal IDs; separate equal-content hit buffers.");
}

template <typename Visitor>
void describe_maps(Visitor visitor)
{
    visitor("baseline", "runtime");
    for_each_family([&visitor](const char* name, auto, const char* description) {
        visitor(std::string("collection.") + name, description);
    });
    visitor("note.operations",
        "Times are CPU ns/key. Builds include construction, reservation and destruction; "
        "erase_insert includes both operations; clear_refill includes clearing and refilling.");
    visitor("note.hashes", "Default hashes and allocation policies differ; exceptions are described per collection.");
    visitor("note.memory",
        "Memory is collected separately from timing. Requested bytes exclude dataset storage, borrowed "
        "pointees, tracking bookkeeping, allocator metadata/rounding and process RSS.");
    visitor("warning.duplicate_after_deletion",
        "Known functional defect: u32 can insert a duplicate key after deletion. "
        "Other specializations share its core but were not independently reproduced. "
        "The separate reproducer is not run during collection.");
}
}

#endif
