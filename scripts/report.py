"""Validate complete repeated benchmark results and compare only within a run."""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics


IMPLEMENTATIONS = ("runtime", "std", "abseil")
NANOSECONDS = {"ns": 1, "us": 1000, "ms": 1_000_000, "s": 1_000_000_000}
MEMORY_CONTEXT_FIELDS = (
    "compiler", "build_type", "system", "architecture", "pointer_bits", "runtime_simd",
    "dataset_seed", "stdlib", "sanitizers", "runtime_revision", "abseil_revision",
    "benchmark_revision",
)


def build_settings(path):
    settings = {}
    names = {
        "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_C_COMPILER_TARGET",
        "CMAKE_CXX_COMPILER_TARGET", "CMAKE_OSX_ARCHITECTURES",
        "CMAKE_OSX_DEPLOYMENT_TARGET", "CMAKE_GENERATOR_PLATFORM",
        "CMAKE_MSVC_RUNTIME_LIBRARY", "CMAKE_INTERPROCEDURAL_OPTIMIZATION",
    }
    for line in path.read_text().splitlines():
        if line.startswith(("#", "//")) or "=" not in line:
            continue
        declaration, value = line.split("=", 1)
        name = declaration.split(":", 1)[0]
        if name in names or name.startswith(("CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS")):
            settings[name] = value
    for name in ("CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS"):
        if name not in settings:
            raise ValueError("Missing build setting: " + name)
    return settings


def summarize(document, metadata):
    if metadata["repetitions"] < 3:
        raise ValueError("At least three repetitions are required")
    expected = set(metadata["expected_benchmarks"])
    if len(expected) != len(metadata["expected_benchmarks"]) or not expected:
        raise ValueError("Expected benchmark names must be nonempty and unique")
    context = document["context"]
    if context["build_type"] != "Release" or context["sanitizers"] != "0":
        raise ValueError("Performance reports require a Release build without sanitizers")
    if context["runtime_simd"] not in ("neon", "sse2"):
        raise ValueError("Unexpected runtime SIMD implementation")
    for dependency, revision in metadata["dependencies"].items():
        if context[dependency + "_revision"] != revision["commit"]:
            raise ValueError("Binary/dependency revision mismatch: " + dependency)
        if revision["dirty"]:
            raise ValueError("Dependency has uncommitted changes: " + dependency)
    samples = {name: {} for name in expected}
    for row in document["benchmarks"]:
        if row.get("error_occurred") or row.get("skipped"):
            raise ValueError("Benchmark failed or skipped: " + row["name"])
        if row.get("run_type") == "aggregate":
            continue
        name = row.get("run_name", row["name"])
        if name not in samples:
            raise ValueError("Unexpected benchmark: " + name)
        repetition = row["repetition_index"]
        if repetition in samples[name]:
            raise ValueError("Duplicate repetition: " + name)
        if not isinstance(repetition, int) or not 0 <= repetition < metadata["repetitions"]:
            raise ValueError("Invalid repetition index: " + name)
        if row["iterations"] <= 0:
            raise ValueError("Benchmark has no iterations: " + name)
        ns = row["cpu_time"] * NANOSECONDS[row["time_unit"]]
        if not math.isfinite(ns) or ns <= 0:
            raise ValueError("Invalid CPU time: " + name)
        samples[name][repetition] = ns
    groups = {}
    for name, repetitions in sorted(samples.items()):
        if len(repetitions) != metadata["repetitions"]:
            raise ValueError("Missing repetitions: " + name)
        implementation, scenario, count_text = name.split("/")
        if implementation not in IMPLEMENTATIONS or int(count_text) <= 0:
            raise ValueError("Invalid benchmark name: " + name)
        times = [repetitions[index] / int(count_text) for index in sorted(repetitions)]
        groups.setdefault((scenario, int(count_text)), {})[implementation] = {
            "median_ns_per_key": statistics.median(times),
            "cv_percent": statistics.stdev(times) / statistics.mean(times) * 100,
            "samples_ns_per_key": times,
        }
    rows = []
    for (scenario, count), implementations in sorted(groups.items()):
        if set(implementations) != set(IMPLEMENTATIONS):
            raise ValueError("Missing comparison implementation: " + scenario)
        baseline = implementations["runtime"]["median_ns_per_key"]
        for implementation in IMPLEMENTATIONS:
            result = implementations[implementation]
            rows.append({
                "scenario": scenario,
                "size": count,
                "implementation": implementation,
                **result,
                "speedup_vs_runtime": baseline / result["median_ns_per_key"],
            })
    return rows


def validate_memory(document, context, expected_names):
    if document["schema_version"] != 1:
        raise ValueError("Unsupported memory-accounting schema")
    for key in MEMORY_CONTEXT_FIELDS:
        if document["context"][key] != context[key]:
            raise ValueError("CPU/memory configuration mismatch: " + key)
    fields = (
        "object_bytes", "before_heap_bytes", "after_heap_bytes", "after_destroy_heap_bytes",
        "allocations", "deallocations", "allocated_bytes", "freed_bytes",
        "end_heap_bytes", "peak_heap_bytes",
    )
    cases = []
    seen = set()
    object_sizes = {}
    for row in document["cases"]:
        name = row["name"]
        if name in seen or name not in expected_names:
            raise ValueError("Duplicate or unexpected memory case: " + name)
        seen.add(name)
        if any(type(row[key]) is not int or row[key] < 0 for key in fields):
            raise ValueError("Invalid memory counters: " + name)
        implementation, scenario, count = name.split("/")
        size = int(count)
        if (row["before_heap_bytes"] + row["allocated_bytes"] - row["freed_bytes"]
                != row["end_heap_bytes"]):
            raise ValueError("Unbalanced memory bytes: " + name)
        if row["peak_heap_bytes"] < max(
                row["before_heap_bytes"], row["after_heap_bytes"], row["end_heap_bytes"]):
            raise ValueError("Invalid memory peak: " + name)
        if row["after_destroy_heap_bytes"] != 0:
            raise ValueError("Memory case leaked allocations: " + name)
        build = scenario in ("build_growing", "build_reserved")
        if build:
            if (row["before_heap_bytes"] != 0 or row["end_heap_bytes"] != 0
                    or row["allocations"] != row["deallocations"]):
                raise ValueError("Invalid build memory boundary: " + name)
        elif row["end_heap_bytes"] != row["after_heap_bytes"]:
            raise ValueError("Invalid operation memory boundary: " + name)
        if implementation in object_sizes and object_sizes[implementation] != row["object_bytes"]:
            raise ValueError("Inconsistent memory object size: " + name)
        object_sizes[implementation] = row["object_bytes"]
        footprint = row["object_bytes"] + row["after_heap_bytes"]
        cases.append({
            **row, "implementation": implementation, "scenario": scenario, "size": size,
            "footprint_bytes": footprint,
            "footprint_bytes_per_key": footprint / size,
            "peak_footprint_bytes": row["object_bytes"] + row["peak_heap_bytes"],
        })
    if seen != set(expected_names):
        raise ValueError("Missing memory cases")
    sizes = {row["size"] for row in cases}
    expected_lifecycles = {
        (implementation, mode, size)
        for implementation in IMPLEMENTATIONS for mode in ("growing", "reserved") for size in sizes
    }
    seen_lifecycles = set()
    for row in document["lifecycles"]:
        key = (row["implementation"], row["mode"], row["size"])
        if key not in expected_lifecycles or key in seen_lifecycles:
            raise ValueError("Duplicate or unexpected memory lifecycle")
        seen_lifecycles.add(key)
        fields = ("object_bytes", "empty_heap_bytes", "populated_heap_bytes",
                  "cleared_heap_bytes", "destroyed_heap_bytes")
        if any(type(row[field]) is not int or row[field] < 0 for field in fields):
            raise ValueError("Invalid memory lifecycle counters")
        if row["destroyed_heap_bytes"] != 0:
            raise ValueError("Memory lifecycle leaked allocations")
        if row["object_bytes"] != object_sizes[row["implementation"]]:
            raise ValueError("Inconsistent lifecycle object size")
    if seen_lifecycles != expected_lifecycles:
        raise ValueError("Missing memory lifecycles")
    return {"cases": cases, "lifecycles": document["lifecycles"]}


def memory_markdown(cases):
    sizes = {}
    for row in cases:
        sizes.setdefault(row["size"], {})[row["implementation"]] = row
    lines = [
        "### Memory accounting",
        "",
        "Post-batch collection footprint: inline object bytes plus live requested heap bytes.",
        "This is a separate instrumented run; no instrumented timings are reported.",
        "",
        "| Keys | Runtime B/key | std B/key | Abseil B/key |",
        "|---:|---:|---:|---:|",
    ]
    for size, implementations in sorted(sizes.items()):
        lowest = min(row["footprint_bytes_per_key"] for row in implementations.values())
        cells = [str(size)]
        for name in IMPLEMENTATIONS:
            value = implementations[name]["footprint_bytes_per_key"]
            cell = f"{value:.3f}"
            cells.append(f"**{cell}**" if value == lowest else cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "<details>",
        "<summary>Memory details: exact bytes and allocation traffic</summary>",
        "",
        "| Keys | Measure | Runtime | std | Abseil |",
        "|---:|---|---:|---:|---:|",
    ]
    metrics = (
        ("object_bytes", "Inline object bytes"),
        ("before_heap_bytes", "Heap bytes before measured batch"),
        ("after_heap_bytes", "Heap bytes after batch, before destruction"),
        ("peak_footprint_bytes", "Peak footprint bytes"),
        ("allocations", "Allocations in measured batch"),
        ("deallocations", "Deallocations in measured batch"),
        ("allocated_bytes", "Bytes allocated in measured batch"),
        ("freed_bytes", "Bytes freed in measured batch"),
        ("after_destroy_heap_bytes", "Heap bytes after destruction"),
    )
    for size, implementations in sorted(sizes.items()):
        for key, label in metrics:
            cells = [str(size), label]
            cells += [f"{implementations[name][key]:,}" for name in IMPLEMENTATIONS]
            lines.append("| " + " | ".join(cells) + " |")
    return lines + ["", "</details>", ""]


def lifecycle_markdown(lifecycles):
    lines = [
        "## Lifecycle memory checkpoints",
        "",
        "Separate probes record empty, populated, cleared, and destroyed collections.",
        "Live-state footprints include inline object bytes; destroyed collections have zero footprint.",
        "",
    ]
    for mode in ("growing", "reserved"):
        sizes = {}
        for row in lifecycles:
            if row["mode"] == mode:
                sizes.setdefault(row["size"], {})[row["implementation"]] = row
        lines += [
            f"### {mode}",
            "",
            "| Keys | State | Runtime bytes | std bytes | Abseil bytes |",
            "|---:|---|---:|---:|---:|",
        ]
        for size, implementations in sorted(sizes.items()):
            for state in ("empty", "populated", "cleared", "destroyed"):
                cells = [str(size), state]
                for name in IMPLEMENTATIONS:
                    row = implementations[name]
                    footprint = row[state + "_heap_bytes"]
                    if state != "destroyed":
                        footprint += row["object_bytes"]
                    cells.append(f"{footprint:,}")
                lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def markdown(rows, context, metadata, memory=None):
    lines = [
        "# Collection benchmark results",
        "",
        "These exploratory measurements help the reader consider:",
        "",
        "> On this configuration, for this runtime-derived workload, how do the alternatives compare with runtime?",
        "",
        "The summary supplies data, not an answer to that interpretive question or a decision to",
        "keep, improve, or replace the runtime collection. Reaching those conclusions requires",
        "the reader's judgment, informed by the collection contract and application requirements.",
        "",
        "Ratios compare implementations in this run only; higher speedup is faster.",
        "Times are CPU ns/key. Build scenarios include construction, reservation, and destruction;",
        "erase_insert includes both operations per key; clear_refill includes clearing and refilling.",
        "Default hashes and allocation policies differ. CV is sample standard deviation / mean.",
        "",
        "**Bold** marks the lowest median time or accounted footprint in each row (including ties).",
        "Speedup is relative to runtime: above 1x is faster; below 1x is slower.",
        "[!] marks CV above 5%; exact values are in the variability details below.",
        "",
        f"Compiler: {context['compiler']}; STL: {context['stdlib']}; "
        f"target: {context['system']} {context['architecture']} "
        f"({context['pointer_bits']}-bit); runtime SIMD: {context['runtime_simd']}.",
        f"Repetitions: {metadata['repetitions']}; minimum CPU seconds/sample: {metadata['min_time']}.",
        "",
    ]
    if metadata.get("functional_tests") == "not_run":
        lines += [
            "**Functional correctness:** not run during data collection. The functional suite",
            "is reserved for separate Linux GCC and Clang validation.",
            "",
        ]
    if metadata["known_duplicate_defect_reproduced"] is None:
        lines += [
            "**Known functional defect:** duplicate keys can be inserted after deletion.",
            "The separate reproducer was not run during data collection.",
            "",
        ]
    elif metadata["known_duplicate_defect_reproduced"]:
        lines += [
            "**Known functional defect:** the separate duplicate-after-deletion reproducer",
            "detected an existing key being inserted again after deletion.",
            "",
        ]
    if any(row["cv_percent"] > 5 for row in rows):
        lines += ["**Noise warning:** some measurements have CV above 5%; inspect raw samples.", ""]
    for warning in metadata.get("measurement_warnings", []):
        lines += [f"**Framework warning:** {warning}", ""]
    if memory is not None:
        lines += [
            "**Memory accounting:** performance and memory are separate runs of the same configuration.",
            "Each memory scenario executes one checked batch; builds include construction and destruction,",
            "while other scenarios exclude initial population and final destruction from traffic counts.",
            "Footprint is sampled before destruction; peak includes pre-existing storage and transient growth.",
            "Requested bytes exclude dataset storage, borrowed pointees, tracking bookkeeping, allocator",
            "metadata/rounding, and process RSS. Stateless counting allocators preserve inline object sizes.",
            "",
        ]
    scenarios = {}
    for row in rows:
        scenarios.setdefault(row["scenario"], {}).setdefault(row["size"], {})[
            row["implementation"]
        ] = row
    for scenario, sizes in sorted(scenarios.items()):
        lines += [
            f"## {scenario}",
            "",
            "### Performance",
            "",
            "| Keys | Runtime ns/key | std ns/key (speedup) | Abseil ns/key (speedup) |",
            "|---:|---:|---:|---:|",
        ]
        for size, implementations in sorted(sizes.items()):
            fastest = min(row["median_ns_per_key"] for row in implementations.values())
            cells = [str(size)]
            for implementation in IMPLEMENTATIONS:
                row = implementations[implementation]
                cell = f"{row['median_ns_per_key']:.3f}"
                if row["median_ns_per_key"] == fastest:
                    cell = f"**{cell}**"
                if implementation != "runtime":
                    cell += f" ({row['speedup_vs_runtime']:.2f}x)"
                if row["cv_percent"] > 5:
                    cell += " [!]"
                cells.append(cell)
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        if memory is not None:
            lines += memory_markdown([
                row for row in memory["cases"] if row["scenario"] == scenario
            ])
    if memory is not None:
        lines += lifecycle_markdown(memory["lifecycles"])
    lines += [
        "<details>",
        "<summary>Variability details (CV %)</summary>",
        "",
        "| Scenario | Keys | Runtime CV % | std CV % | Abseil CV % |",
        "|---|---:|---:|---:|---:|",
    ]
    for scenario, sizes in sorted(scenarios.items()):
        for size, implementations in sorted(sizes.items()):
            cells = [scenario, str(size)]
            cells += [f"{implementations[name]['cv_percent']:.2f}" for name in IMPLEMENTATIONS]
            lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def write_reports(directory):
    document = json.loads((directory / "benchmarks.json").read_text())
    metadata = json.loads((directory / "metadata.json").read_text())
    rows = summarize(document, metadata)
    memory = None
    memory_files = (directory / "memory.json", directory / "memory-metadata.json")
    if any(path.exists() for path in memory_files):
        if not all(path.exists() for path in memory_files):
            raise ValueError("Incomplete memory artifacts")
        memory_document = json.loads(memory_files[0].read_text())
        memory_metadata = json.loads(memory_files[1].read_text())
        if (build_settings(directory / "CMakeCache.txt")
                != build_settings(directory / "memory-CMakeCache.txt")):
            raise ValueError("CPU/memory build settings mismatch")
        for filename in ("benchmarks.json", "metadata.json"):
            digest = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
            if memory_metadata["cpu_artifact_hashes"][filename] != digest:
                raise ValueError("Memory accounting belongs to different CPU results: " + filename)
        for dependency, revision in memory_metadata["dependencies"].items():
            if revision["dirty"] or revision["commit"] != metadata["dependencies"][dependency]["commit"]:
                raise ValueError("Memory dependency mismatch: " + dependency)
        memory = validate_memory(memory_document, document["context"], metadata["expected_benchmarks"])
    text = markdown(rows, document["context"], metadata, memory)
    (directory / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    output = io.StringIO(newline="")
    fields = [key for key in rows[0] if key != "samples_ns_per_key"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    (directory / "summary.csv").write_text(output.getvalue())
    (directory / "summary.md").write_text(text)
    if memory is not None:
        (directory / "memory-summary.json").write_text(json.dumps(memory, indent=2) + "\n")
        for filename, entries in (("memory-summary.csv", memory["cases"]),
                                  ("memory-lifecycle.csv", memory["lifecycles"])):
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=list(entries[0]))
            writer.writeheader()
            writer.writerows(entries)
            (directory / filename).write_text(output.getvalue())
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    try:
        count = write_reports(args.results)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Report failed: {error}\n")
    print(f"Wrote {count} comparison rows to {args.results}")


if __name__ == "__main__":
    main()
