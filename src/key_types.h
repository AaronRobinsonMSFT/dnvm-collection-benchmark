#ifndef DNVM_KEY_TYPES_H
#define DNVM_KEY_TYPES_H

#include <cstdint>
#include <cstring>
#include <functional>
#include <string_view>

#include "absl/hash/hash.h"
#include "dn-simdhash-specializations.h"

namespace dnvm
{
template <typename Key>
struct KeyTraits
{
    using Hash = std::hash<Key>;
    using AbseilHash = absl::Hash<Key>;
    using Equal = std::equal_to<Key>;
};

struct PointerPairHash
{
    std::size_t operator()(dn_ptrpair_t key) const
    {
        return absl::HashOf(key.first, key.second);
    }
};

struct PointerPairEqual
{
    bool operator()(dn_ptrpair_t left, dn_ptrpair_t right) const
    {
        return left.first == right.first && left.second == right.second;
    }
};

template <>
struct KeyTraits<dn_ptrpair_t>
{
    using Hash = PointerPairHash;
    using AbseilHash = PointerPairHash;
    using Equal = PointerPairEqual;
};

struct CStringHash
{
    std::size_t operator()(const char* key) const
    {
        return std::hash<std::string_view>{}(key);
    }
};

struct CStringAbseilHash
{
    std::size_t operator()(const char* key) const
    {
        return absl::Hash<std::string_view>{}(key);
    }
};

struct CStringEqual
{
    bool operator()(const char* left, const char* right) const
    {
        return std::strcmp(left, right) == 0;
    }
};

template <>
struct KeyTraits<const char*>
{
    using Hash = CStringHash;
    using AbseilHash = CStringAbseilHash;
    using Equal = CStringEqual;
};

template <typename Key>
std::uint64_t key_checksum(Key key)
{
    return typename KeyTraits<Key>::Hash{}(key);
}

template <>
inline std::uint64_t key_checksum(std::uint32_t key)
{
    return key;
}
}

#endif
