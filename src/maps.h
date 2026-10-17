#ifndef DNVM_MAPS_H
#define DNVM_MAPS_H

#include <cstdint>
#include <new>
#include <stdexcept>
#include <unordered_map>
#include <utility>

#include "absl/container/flat_hash_map.h"
#include "dn-simdhash-specializations.h"

namespace dnvm
{
class RuntimeMap
{
public:
    explicit RuntimeMap(std::uint32_t capacity = 0)
        : map_(dn_simdhash_u32_ptr_new(capacity, nullptr))
    {
        if (map_ == nullptr)
            throw std::bad_alloc();
    }

    ~RuntimeMap() { dn_simdhash_free(map_); }
    RuntimeMap(const RuntimeMap&) = delete;
    RuntimeMap& operator=(const RuntimeMap&) = delete;

    bool insert(std::uint32_t key, void* value)
    {
        switch (dn_simdhash_u32_ptr_try_add(map_, key, value))
        {
            case DN_SIMDHASH_ADD_INSERTED: return true;
            case DN_SIMDHASH_ADD_FAILED: return false;
            case DN_SIMDHASH_OUT_OF_MEMORY: throw std::bad_alloc();
            default: throw std::runtime_error("runtime map insertion failed internally");
        }
    }

    bool find(std::uint32_t key, void*& value) const
    {
        return dn_simdhash_u32_ptr_try_get_value(map_, key, &value) != 0;
    }

    bool replace(std::uint32_t key, void* value)
    {
        return dn_simdhash_u32_ptr_try_replace_value(map_, key, value) != 0;
    }

    bool erase(std::uint32_t key) { return dn_simdhash_u32_ptr_try_remove(map_, key) != 0; }
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
        dn_simdhash_u32_ptr_foreach(map_, [](std::uint32_t key, void* value, void* context) {
            (*static_cast<Visitor*>(context))(key, value);
        }, &visitor);
    }

private:
    dn_simdhash_u32_ptr_t* map_;
};

template <typename Map>
class StandardMap
{
public:
    explicit StandardMap(std::uint32_t capacity = 0)
    {
        if (capacity != 0)
            map_.reserve(capacity);
    }

    bool insert(std::uint32_t key, void* value) { return map_.emplace(key, value).second; }
    bool find(std::uint32_t key, void*& value) const
    {
        const typename Map::const_iterator found = map_.find(key);
        if (found == map_.end())
            return false;
        value = found->second;
        return true;
    }

    bool replace(std::uint32_t key, void* value)
    {
        const typename Map::iterator found = map_.find(key);
        if (found == map_.end())
            return false;
        found->second = value;
        return true;
    }

    bool erase(std::uint32_t key) { return map_.erase(key) != 0; }
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
}

#endif
