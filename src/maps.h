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
#include "coreclr_maps.h"
#include "runtime_umap.h"

namespace dnvm
{
template <typename Key, auto New, auto Add, auto Find, auto Replace, auto Remove, auto Foreach>
class RuntimeMapAdapter
{
public:
    using key_type = Key;
    static constexpr bool supports_replace = Replace != nullptr;
    static constexpr bool supports_remove = true;
    static constexpr bool allows_duplicates = false;
    static constexpr bool is_set = false;

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
    static constexpr bool supports_remove = true;
    static constexpr bool allows_duplicates = false;
    static constexpr bool is_set = false;

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
using UMapMaps = MapFamily<RuntimeUMap>;

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
    visitor("shash", CoreClrFamily<CoreSHash>{},
        "CoreCLR SHash<MapSHashTraits<uint32_t, void*>>; duplicate keys retained; removal erases all matches. "
        "Keys 0 and UINT32_MAX are reserved. STL unordered_multimap; Abseil btree_multimap.");
    visitor("mapshash", CoreClrFamily<CoreMapSHash>{},
        "CoreCLR MapSHash<uint32_t, void*> with default no-remove traits; duplicate keys retained; "
        "key 0 is reserved. STL unordered_multimap; Abseil btree_multimap.");
    visitor("setshash", CoreClrFamily<CoreSetSHash>{},
        "CoreCLR SetSHash<uint32_t> with default no-remove traits; duplicate keys retained; key 0 is reserved. "
        "STL unordered_multiset; Abseil btree_multiset. No mapped values.");
    visitor("umap", UMapMaps{},
        "dn_umap; separate-chaining hash table; borrowed pointer keys/values; default direct hash. "
        "Reserve requests buckets. Replacement checks presence before dn_umap_insert_or_assign.");
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
    visitor("note.datasets",
        "Datasets are generated once per key type and size and reused within each measurement process. "
        "CPU and memory collection run in separate processes; collection state is not shared.");
    visitor("note.pointer_datasets",
        "Pointer datasets use pointer-sized slots; pair second components use deterministic seeded indices. "
        "Hit/miss sets are disjoint. Before population, pair hit and miss sets each require at least 90% "
        "distinct dn_simdhash_ptrpair_ptr full hashes and no hash shared by more than max(4, size/100) keys. "
        "Pointer addresses and actual hashes vary by process. Older pointer/pair results used a different layout.");
    visitor("note.memory",
        "Memory is collected separately from timing. Requested bytes exclude dataset storage, borrowed "
        "pointees, tracking bookkeeping, allocator metadata/rounding and process RSS.");
    visitor("note.coreclr",
        "SHash headers and iterators are compiled unchanged with standalone type/contract declarations; "
        "VM contracts/DAC infrastructure are not included. CoreCLR SHash Add retains duplicates. "
        "STL alternatives are hash containers; Abseil alternatives are B-trees and have no reserve operation. "
        "SHash reserve converts element count to slots using the SHash density policy. "
        "Replacement/erase timings use unique-key populations; key-specific lookup among duplicates is unordered.");
    visitor("scenario.lookup_hit",
        "Look up every key in a populated map/set using queries that are all present. "
        "Initial population is excluded from timing.");
    visitor("scenario.lookup_miss",
        "Look up keys that are all absent from a populated map/set. Initial population is excluded from timing.");
    visitor("scenario.build_growing",
        "Construct a collection, insert or append all entries without requesting capacity in advance, "
        "then destroy it. Timing includes construction, growth, and destruction.");
    visitor("scenario.build_reserved",
        "Construct a collection with capacity requested for the full dataset, populate it, then destroy it. "
        "Timing includes the capacity request, population, and destruction.");
    visitor("scenario.duplicate",
        "Attempt to insert every existing key again into a collection that rejects duplicates. "
        "Measures duplicate detection without adding entries; initial population is excluded from timing.");
    visitor("scenario.duplicate_build",
        "Construct a duplicate-preserving map/set, insert the dataset twice so each key has two entries, "
        "then destroy it. Timing includes construction, both insertion passes, and destruction. "
        "Size and memory footprint are per distinct key, not per entry.");
    visitor("scenario.replace",
        "Find each existing map key and replace its mapped pointer value. Keys and entry count remain unchanged; "
        "initial population is excluded from timing.");
    visitor("scenario.erase_insert",
        "Remove each existing key and immediately insert it again. Measures removal and reinsertion together; "
        "initial population is excluded from timing.");
    visitor("scenario.iterate",
        "Visit every entry or element in a populated collection using that implementation's traversal API. "
        "Construction and population are excluded from timing.");
    visitor("scenario.clear_refill",
        "Clear a populated collection and refill it with the same dataset. Measures clearing and refilling "
        "together, including any resulting allocations; each implementation retains or releases storage "
        "according to its own clear behavior.");
    visitor("note.scenarios",
        "Not every collection supports every scenario. CPU timing and memory accounting are collected separately. "
        "Sequence elements and map pointer values are borrowed; these workloads do not construct or destroy "
        "the pointed-to objects.");
    visitor("warning.duplicate_after_deletion",
        "Known functional defect: u32 can insert a duplicate key after deletion. "
        "Other specializations share its core but were not independently reproduced. "
        "The separate reproducer is not run during collection.");
    visitor("warning.umap_rehash",
        "dn_umap rehash does not preserve its bucket array on allocation failure. "
        "Such failure is fatal, not a recoverable benchmark result.");
}
}

#endif
