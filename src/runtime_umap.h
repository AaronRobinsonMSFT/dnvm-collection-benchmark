#ifndef DNVM_RUNTIME_UMAP_H
#define DNVM_RUNTIME_UMAP_H

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <stdexcept>
#include "dn-umap.h"

namespace dnvm
{
class RuntimeUMap
{
public:
    using key_type = void*;
    static constexpr bool supports_replace = true;
    static constexpr bool supports_remove = true;
    static constexpr bool allows_duplicates = false;
    static constexpr bool is_set = false;

    explicit RuntimeUMap(std::uint32_t capacity = 0, dn_allocator_t* allocator = nullptr)
    {
        dn_umap_custom_alloc_params_t params{};
        params.allocator = allocator;
        map_ = dn_umap_custom_alloc(&params);
        if (map_ == nullptr)
            throw std::bad_alloc();
        if (capacity != 0)
            reserve(capacity);
    }
    ~RuntimeUMap() { dn_umap_free(map_); }
    RuntimeUMap(const RuntimeUMap&) = delete;
    RuntimeUMap& operator=(const RuntimeUMap&) = delete;

    bool insert(void* key, void* value)
    {
        const auto result = dn_umap_insert(map_, key, value);
        if (result.it._internal._node == nullptr)
            throw std::bad_alloc();
        return result.result;
    }
    bool find(void* key, void*& value) const
    {
        const auto found = dn_umap_find(map_, key);
        if (dn_umap_it_end(found))
            return false;
        value = dn_umap_it_value(found);
        return true;
    }
    bool replace(void* key, void* value)
    {
        const auto found = dn_umap_find(map_, key);
        if (dn_umap_it_end(found))
            return false;
        dn_umap_insert_or_assign(map_, key, value);
        return true;
    }
    bool erase(void* key) { return dn_umap_erase_key(map_, key) != 0; }
    std::uint32_t size() const { return dn_umap_size(map_); }
    void clear() { dn_umap_clear(map_); }
    void reserve(std::uint32_t count)
    {
        if (count == 0)
            throw std::invalid_argument("dn_umap requires a nonzero bucket count");
        dn_umap_reserve(map_, count);
        if (map_->_internal._buckets == nullptr)
        {
            std::fputs("dn_umap reserve failed: native rehash lost its bucket array\n", stderr);
            std::abort();
        }
    }

    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        dn_umap_for_each(map_, [](void* key, void* value, void* context) {
            (*static_cast<Visitor*>(context))(key, value);
        }, &visitor);
    }

private:
    dn_umap_t* map_;
};
}

#endif
