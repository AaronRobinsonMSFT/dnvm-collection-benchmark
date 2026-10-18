#ifndef DNVM_SEQUENCE_BENCHMARKS_H
#define DNVM_SEQUENCE_BENCHMARKS_H

#include <benchmark/benchmark.h>
#include "dataset.h"
#include "sequences.h"

namespace
{
template <typename Sequence>
void sequence_batch(benchmark::State& state, const std::string& scenario)
{
    const auto count = static_cast<std::uint32_t>(state.range(0));
    const auto& values = dnvm::shared_dataset<void*>(count).hits;
    const auto& indices = dnvm::shared_dataset<std::uint32_t>(count).hits;
    auto measure = [&state](auto& sequence, auto operation) {
        for (auto _ : state)
        {
            benchmark::DoNotOptimize(sequence);
            operation();
            benchmark::ClobberMemory();
        }
    };
    if constexpr (Sequence::intrusive)
    {
        Sequence sequence(values);
        sequence.fill();
        if (scenario == "unlink_relink")
        {
            measure(sequence, [&sequence] {
                sequence.clear();
                benchmark::ClobberMemory();
                sequence.fill();
            });
        }
        else if (scenario == "iterate")
        {
            measure(sequence, [&sequence] {
                auto visitor = [](void* value) { benchmark::DoNotOptimize(value); };
                sequence.visit(visitor);
            });
        }
        else
            throw std::invalid_argument("Unknown intrusive-list CPU scenario: " + scenario);
    }
    else
    {
        const bool build = scenario == "build_growing" || scenario == "build_reserved";
        if (build)
        {
            for (auto _ : state)
            {
                Sequence sequence(scenario == "build_reserved" ? count : 0);
                dnvm::populate_sequence(sequence, values);
                benchmark::DoNotOptimize(sequence);
                benchmark::ClobberMemory();
            }
        }
        else
        {
            Sequence sequence;
            dnvm::populate_sequence(sequence, values);
            if (scenario == "index_read")
            {
                measure(sequence, [&] {
                    for (auto index : indices)
                        benchmark::DoNotOptimize(sequence.get(index % count));
                });
            }
            else if (scenario == "index_write")
            {
                measure(sequence, [&] {
                    for (std::uint32_t i = 0; i < count; ++i)
                        sequence.set(indices[i] % count, values[(i + 1) % count]);
                });
            }
            else if (scenario == "clear_refill")
            {
                measure(sequence, [&] {
                    sequence.clear();
                    dnvm::populate_sequence(sequence, values);
                });
            }
            else if (scenario == "iterate")
            {
                measure(sequence, [&sequence] {
                    auto visitor = [](void* value) { benchmark::DoNotOptimize(value); };
                    sequence.visit(visitor);
                });
            }
            else
                throw std::invalid_argument("Unknown sequence CPU scenario: " + scenario);
        }
    }
    state.SetItemsProcessed(state.iterations() * count);
}

void register_sequences()
{
    dnvm::for_each_sequence_family([](const char* collection, auto family, const char*) {
        family.implementations([collection](const char* implementation, auto tag) {
            using Sequence = typename decltype(tag)::type;
            dnvm::for_each_sequence_scenario<Sequence>([collection, implementation](const char* scenario) {
                for (std::int64_t size : {8, 64, 1024, 65536})
                {
                    const auto name = std::string(collection) + "/" + implementation + "/" + scenario;
                    benchmark::RegisterBenchmark(name.c_str(), [scenario](benchmark::State& state) {
                        try
                        {
                            sequence_batch<Sequence>(state, scenario);
                        }
                        catch (const std::exception& error)
                        {
                            state.SkipWithError(error.what());
                        }
                    })->Arg(size)->Unit(benchmark::kNanosecond);
                }
            });
        });
    });
}
}

#endif
