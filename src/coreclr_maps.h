#ifndef DNVM_CORECLR_MAPS_H
#define DNVM_CORECLR_MAPS_H

#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

#include "absl/container/btree_map.h"
#include "absl/container/btree_set.h"
#include "coreclr_compat.h"

namespace dnvm
{
template <typename Container, bool IsSet, bool SupportsRemove>
class CoreClrMap
{
public:
    using key_type = std::uint32_t;
    static constexpr bool supports_replace = !IsSet;
    static constexpr bool supports_remove = SupportsRemove;
    static constexpr bool allows_duplicates = true;
    static constexpr bool is_set = IsSet;

    explicit CoreClrMap(std::uint32_t capacity = 0)
    {
        if (capacity != 0)
            reserve(capacity);
    }
    CoreClrMap(const CoreClrMap&) = delete;
    CoreClrMap& operator=(const CoreClrMap&) = delete;

    bool insert(key_type key, void* value)
    {
        validate_key(key);
        if constexpr (IsSet)
            map_.Add(key);
        else if constexpr (!SupportsRemove)
            map_.Add(key, value);
        else
            map_.Add(typename Container::element_t(key, value));
        return true;
    }

    bool find(key_type key, void*& value) const
    {
        validate_key(key);
        const typename Container::element_t* entry = map_.LookupPtr(key);
        if (entry == nullptr)
            return false;
        if constexpr (IsSet)
            value = nullptr;
        else
            value = entry->Value();
        return true;
    }

    bool replace(key_type key, void* value)
    {
        static_assert(supports_replace, "Sets have no mapped values");
        validate_key(key);
        const typename Container::element_t* entry = map_.LookupPtr(key);
        if (entry == nullptr)
            return false;
        map_.ReplacePtr(entry, typename Container::element_t(key, value));
        return true;
    }

    bool erase(key_type key)
    {
        static_assert(supports_remove, "Removal is not enabled for this specialization");
        validate_key(key);
        const std::uint32_t before = map_.GetCount();
        if (map_.LookupPtr(key) != nullptr)
            map_.Remove(key);
        return map_.GetCount() != before;
    }

    std::size_t size() const { return map_.GetCount(); }
    void clear() { map_.RemoveAll(); }
    void reserve(std::uint32_t count)
    {
        const std::uint64_t slots = (std::uint64_t(count) * Container::s_density_factor_denominator
            + Container::s_density_factor_numerator - 1) / Container::s_density_factor_numerator;
        if (slots > std::numeric_limits<COUNT_T>::max())
            throw std::bad_alloc();
        if (count > map_.GetCapacity())
            map_.Reallocate(static_cast<COUNT_T>(slots));
    }

    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        for (typename Container::Iterator it = map_.Begin(), end = map_.End(); it != end; ++it)
        {
            if constexpr (IsSet)
                visitor(*it, nullptr);
            else
                visitor(it->Key(), it->Value());
        }
    }

private:
    static void validate_key(key_type key)
    {
        if (key == 0 || (SupportsRemove && key == std::numeric_limits<key_type>::max()))
            throw std::invalid_argument("CoreCLR key is reserved as an empty/deleted sentinel");
    }
    Container map_;
};

template <typename Container, bool IsSet, bool SupportsRemove>
class StandardMultiMap
{
public:
    using key_type = typename Container::key_type;
    using container_type = Container;
    static constexpr bool supports_replace = !IsSet;
    static constexpr bool supports_remove = SupportsRemove;
    static constexpr bool allows_duplicates = true;
    static constexpr bool is_set = IsSet;

    explicit StandardMultiMap(std::uint32_t capacity = 0) { reserve(capacity); }
    bool insert(key_type key, void* value)
    {
        if constexpr (IsSet)
            map_.emplace(key);
        else
            map_.emplace(key, value);
        return true;
    }
    bool find(key_type key, void*& value) const
    {
        const auto it = map_.find(key);
        if (it == map_.end())
            return false;
        if constexpr (IsSet)
            value = nullptr;
        else
            value = it->second;
        return true;
    }
    bool replace(key_type key, void* value)
    {
        static_assert(supports_replace, "Sets have no mapped values");
        const auto it = map_.find(key);
        if (it == map_.end())
            return false;
        it->second = value;
        return true;
    }
    bool erase(key_type key)
    {
        static_assert(supports_remove, "Removal is not compared for this family");
        return map_.erase(key) != 0;
    }
    std::size_t size() const { return map_.size(); }
    void clear() { map_.clear(); }
    void reserve(std::uint32_t count)
    {
        if constexpr (IsUnordered<Container>::value)
        {
            if (count != 0)
                map_.reserve(count);
        }
    }
    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        for (const auto& entry : map_)
        {
            if constexpr (IsSet)
                visitor(entry, nullptr);
            else
                visitor(entry.first, entry.second);
        }
    }

private:
    template <typename T, typename = void> struct IsUnordered : std::false_type {};
    template <typename T> struct IsUnordered<T, std::void_t<typename T::hasher>> : std::true_type {};
    Container map_;
};

using CoreSHash = CoreClrMap<SHash<MapSHashTraits<std::uint32_t, void*>>, false, true>;
using CoreMapSHash = CoreClrMap<MapSHash<std::uint32_t, void*>, false, false>;
using CoreSetSHash = CoreClrMap<SetSHash<std::uint32_t>, true, false>;

template <typename Runtime>
struct CoreClrFamily
{
    static constexpr bool is_set = Runtime::is_set;
    using RuntimeMap = Runtime;
    using StdContainer = std::conditional_t<is_set,
        std::unordered_multiset<std::uint32_t>, std::unordered_multimap<std::uint32_t, void*>>;
    using AbseilContainer = std::conditional_t<is_set,
        absl::btree_multiset<std::uint32_t>, absl::btree_multimap<std::uint32_t, void*>>;
    using StdMap = StandardMultiMap<StdContainer, is_set, Runtime::supports_remove>;
    using AbseilMap = StandardMultiMap<AbseilContainer, is_set, Runtime::supports_remove>;
};
}

#endif
