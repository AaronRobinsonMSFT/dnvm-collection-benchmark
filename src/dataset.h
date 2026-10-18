#ifndef DNVM_DATASET_H
#define DNVM_DATASET_H

#include <algorithm>
#include <array>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "key_types.h"
#include "dn-simdhash-utils.h"

namespace dnvm
{
constexpr std::uint32_t DatasetSeed = 0x9e3779b9u;

inline void validate_pointer_pair_hashes(const std::vector<dn_ptrpair_t>& keys)
{
    std::unordered_map<std::uint32_t, std::size_t> hashes;
    hashes.reserve(keys.size());
    const auto limit = std::max<std::size_t>(4, keys.size() / 100);
    for (auto key : keys)
    {
        // The pinned specialization's hasher is private; use its exact component hash/combiner.
        const auto hash = MurmurHash3_32_ptr(key.first, 0) ^ MurmurHash3_32_ptr(key.second, 1);
        if (++hashes[hash] > limit)
            throw std::runtime_error("Pointer-pair benchmark dataset has concentrated native hashes");
    }
    if (hashes.size() < keys.size() - keys.size() / 10)
        throw std::runtime_error("Pointer-pair benchmark dataset has insufficient native hash diversity");
}

inline std::uint32_t dataset_key(std::uint32_t index)
{
    std::uint32_t key = index ^ DatasetSeed;
    key ^= key >> 16;
    key *= 0x85ebca6bu;
    key ^= key >> 13;
    key *= 0xc2b2ae35u;
    return key ^ (key >> 16);
}

template <typename Key>
struct DatasetFor;

template <>
struct DatasetFor<std::uint32_t>
{
    explicit DatasetFor(std::uint32_t count) : values(count)
    {
        hits.reserve(count);
        misses.reserve(count);
        for (std::uint32_t i = 0; i < count; ++i)
        {
            hits.push_back(dataset_key(2 * i));
            misses.push_back(dataset_key(2 * i + 1));
            values[i] = i;
        }
        hit_queries = hits;
    }

    std::vector<std::uint32_t> hits;
    std::vector<std::uint32_t> misses;
    std::vector<std::uint32_t> hit_queries;
    std::vector<std::uint32_t> values;
};

template <>
struct DatasetFor<void*>
{
    explicit DatasetFor(std::uint32_t count) : values(count), keys(count)
    {
        for (std::uint32_t i = 0; i < count; ++i)
        {
            hits.push_back(&keys[i][0]);
            misses.push_back(&keys[i][1]);
            values[i] = i;
        }
        hit_queries = hits;
    }

    std::vector<void*> hits, misses, hit_queries;
    std::vector<std::uint32_t> values;
    std::vector<std::array<std::uintptr_t, 2>> keys;
};

template <>
struct DatasetFor<dn_ptrpair_t>
{
    explicit DatasetFor(std::uint32_t count) : values(count), keys(count)
    {
        for (std::uint32_t i = 0; i < count; ++i)
        {
            const auto second = dataset_key(i) % count;
            hits.push_back({&keys[i][0], &keys[second][1]});
            misses.push_back(i % 2 == 0
                ? dn_ptrpair_t{&keys[i][0], &keys[second][2]}
                : dn_ptrpair_t{&keys[i][3], &keys[second][1]});
            values[i] = i;
        }
        hit_queries = hits;
        validate_pointer_pair_hashes(hits);
        validate_pointer_pair_hashes(misses);
    }

    std::vector<dn_ptrpair_t> hits, misses, hit_queries;
    std::vector<std::uint32_t> values;
    std::vector<std::array<std::uintptr_t, 4>> keys;
};

template <>
struct DatasetFor<const char*>
{
    explicit DatasetFor(std::uint32_t count) : values(count), keys(2 * count), queries(count)
    {
        for (std::uint32_t i = 0; i < count; ++i)
        {
            const std::string prefix((i % 3) * 32, 'k');
            keys[2 * i] = prefix + std::to_string(dataset_key(2 * i));
            keys[2 * i + 1] = prefix + std::to_string(dataset_key(2 * i + 1));
            queries[i] = keys[2 * i];
            hits.push_back(keys[2 * i].c_str());
            misses.push_back(keys[2 * i + 1].c_str());
            hit_queries.push_back(queries[i].c_str());
            values[i] = i;
        }
    }

    std::vector<const char*> hits, misses, hit_queries;
    std::vector<std::uint32_t> values;
    std::vector<std::string> keys, queries;
};

using Dataset = DatasetFor<std::uint32_t>;

template <typename Map>
using MapDataset = DatasetFor<typename Map::key_type>;

template <typename Key>
DatasetFor<Key>& shared_dataset(std::uint32_t count)
{
    // Construct in place: pointer/string keys borrow storage owned by the dataset.
    static std::map<std::uint32_t, DatasetFor<Key>> datasets;
    return datasets.try_emplace(count, count).first->second;
}

template <typename Map, typename Data>
void populate(Map& map, Data& data)
{
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        if (!map.insert(data.hits[i], &data.values[i]))
            throw std::runtime_error("dataset unexpectedly contained a duplicate");
    }
}
}

#endif
