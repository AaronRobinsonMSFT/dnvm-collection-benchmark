#ifndef DNVM_DATASET_H
#define DNVM_DATASET_H

#include <cstdint>
#include <stdexcept>
#include <vector>

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

struct Dataset
{
    explicit Dataset(std::uint32_t count) : values(count)
    {
        hits.reserve(count);
        misses.reserve(count);
        for (std::uint32_t i = 0; i < count; ++i)
        {
            hits.push_back(dataset_key(2 * i));
            misses.push_back(dataset_key(2 * i + 1));
            values[i] = i;
        }
    }

    std::vector<std::uint32_t> hits;
    std::vector<std::uint32_t> misses;
    std::vector<std::uint32_t> values;
};

template <typename Map>
void populate(Map& map, Dataset& data)
{
    for (std::size_t i = 0; i < data.hits.size(); ++i)
    {
        if (!map.insert(data.hits[i], &data.values[i]))
            throw std::runtime_error("dataset unexpectedly contained a duplicate");
    }
}
}

#endif
