#ifndef DNVM_DATASET_H
#define DNVM_DATASET_H

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include "key_types.h"

namespace dnvm
{
constexpr std::uint32_t DatasetSeed = 0x9e3779b9u;

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
    explicit DatasetFor(std::uint32_t count) : values(count), keys(2 * count)
    {
        for (std::uint32_t i = 0; i < count; ++i)
        {
            hits.push_back(&keys[2 * i]);
            misses.push_back(&keys[2 * i + 1]);
            values[i] = i;
        }
        hit_queries = hits;
    }

    std::vector<void*> hits, misses, hit_queries;
    std::vector<std::uint32_t> values, keys;
};

template <>
struct DatasetFor<dn_ptrpair_t>
{
    explicit DatasetFor(std::uint32_t count) : values(count), keys(4 * count)
    {
        for (std::uint32_t i = 0; i < count; ++i)
        {
            hits.push_back({&keys[4 * i], &keys[4 * i + 1]});
            misses.push_back(i % 2 == 0
                ? dn_ptrpair_t{&keys[4 * i], &keys[4 * i + 2]}
                : dn_ptrpair_t{&keys[4 * i + 3], &keys[4 * i + 1]});
            values[i] = i;
        }
        hit_queries = hits;
    }

    std::vector<dn_ptrpair_t> hits, misses, hit_queries;
    std::vector<std::uint32_t> values, keys;
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
