#ifndef DNVM_SEQUENCE_MEASUREMENTS_H
#define DNVM_SEQUENCE_MEASUREMENTS_H

#include "memory_measurements.h"
#include "sequences.h"

namespace dnvm
{
template <typename Sequence, typename Visitor>
void with_sequence(const std::vector<void*>& values, std::uint32_t capacity, Visitor visitor)
{
    if constexpr (Sequence::intrusive)
    {
        Sequence sequence(values);
        visitor(sequence);
    }
    else
    {
        Sequence sequence(capacity);
        visitor(sequence);
    }
}

template <typename Sequence>
void check_sequence(Sequence& sequence, const std::vector<void*>& expected)
{
    std::size_t index = 0;
    auto visitor = [&expected, &index](void* value) {
        memory_check(index < expected.size() && value == expected[index],
                     "Sequence traversal returned the wrong element/order");
        ++index;
    };
    sequence.visit(visitor);
    memory_check(index == expected.size(), "Sequence traversal returned the wrong count");
    if constexpr (!Sequence::intrusive)
    {
        memory_check(sequence.size() == expected.size(), "Sequence size differs from traversal");
        for (std::uint32_t i = 0; i < expected.size(); ++i)
            memory_check(sequence.get(i) == expected[i], "Sequence indexed access differs from traversal");
    }
}

template <typename Sequence>
MemoryMeasurement measure_sequence_memory(const std::string& scenario, std::uint32_t count)
{
    const auto& values = shared_dataset<void*>(count).hits;
    const auto& indices = shared_dataset<std::uint32_t>(count).hits;
    std::vector<void*> expected(values);
    if constexpr (Sequence::intrusive)
        std::reverse(expected.begin(), expected.end());
    else if (scenario == "index_write")
        for (std::uint32_t i = 0; i < count; ++i)
            expected[indices[i] % count] = values[(i + 1) % count];

    MemoryAccount account;
    MemoryScope scope(account);
    MemoryMeasurement result{};
    result.object_bytes = Sequence::object_bytes;
    result.includes_destruction = scenario == "build_growing" || scenario == "build_reserved";
    with_sequence<Sequence>(values, scenario == "build_reserved" ? count : 0, [&](auto& sequence) {
        if (result.includes_destruction)
            populate_sequence(sequence, values);
        else
        {
            populate_sequence(sequence, values);
            account.begin_phase();
            result.before_heap_bytes = account.stats().live_bytes;
            if (scenario == "iterate")
                check_sequence(sequence, expected);
            else if constexpr (Sequence::intrusive)
            {
                memory_check(scenario == "unlink_relink", "Unknown intrusive-list memory scenario");
                sequence.clear();
                memory_check(sequence.empty(), "Unlinking did not empty the list");
                sequence.fill();
            }
            else
            {
                if (scenario == "index_read")
                    for (auto index : indices)
                        memory_check(sequence.get(index % count) == values[index % count],
                                     "Sequence indexed read returned the wrong element");
                else if (scenario == "index_write")
                    for (std::uint32_t i = 0; i < count; ++i)
                        sequence.set(indices[i] % count, values[(i + 1) % count]);
                else if (scenario == "clear_refill")
                {
                    sequence.clear();
                    memory_check(sequence.size() == 0, "Clearing did not empty the sequence");
                    populate_sequence(sequence, values);
                }
                else
                    throw std::invalid_argument("Unknown sequence memory scenario: " + scenario);
            }
        }
        result.after_heap_bytes = account.stats().live_bytes;
        result.traffic = account.stats();
        check_sequence(sequence, expected);
    });
    if (result.includes_destruction)
        result.traffic = account.stats();
    result.after_destroy_heap_bytes = account.stats().live_bytes;
    memory_check(account.live_allocations() == 0, "Sequence memory scenario leaked allocations");
    return result;
}

template <typename Sequence>
LifecycleMeasurement measure_sequence_lifecycle(bool reserved, std::uint32_t count)
{
    const auto& values = shared_dataset<void*>(count).hits;
    MemoryAccount account;
    MemoryScope scope(account);
    LifecycleMeasurement result{};
    result.object_bytes = Sequence::object_bytes;
    with_sequence<Sequence>(values, reserved ? count : 0, [&](auto& sequence) {
        result.empty_heap_bytes = account.stats().live_bytes;
        populate_sequence(sequence, values);
        result.populated_heap_bytes = account.stats().live_bytes;
        sequence.clear();
        if constexpr (Sequence::intrusive)
            memory_check(sequence.empty(), "Sequence lifecycle retained linked nodes");
        else
            memory_check(sequence.size() == 0, "Sequence lifecycle retained elements");
        result.cleared_heap_bytes = account.stats().live_bytes;
    });
    result.destroyed_heap_bytes = account.stats().live_bytes;
    memory_check(account.live_allocations() == 0, "Sequence lifecycle leaked allocations");
    return result;
}
}

#endif
