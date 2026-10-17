"""Validate complete repeated benchmark results and compare only within a run."""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics


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


def split_name(name):
    parts = name.split("/")
    if len(parts) == 3:
        parts.insert(0, "")
    if len(parts) != 4 or not all(parts[1:]):
        raise ValueError("Invalid benchmark name: " + name)
    collection, implementation, scenario, count = parts
    if int(count) <= 0:
        raise ValueError("Invalid benchmark name: " + name)
    return collection, implementation, scenario, int(count)


def comparison_inventory(expected_names):
    inventory = {}
    for name in expected_names:
        collection, implementation, _, _ = split_name(name)
        implementations = inventory.setdefault(collection, [])
        if implementation not in implementations:
            implementations.append(implementation)
    return inventory


def baseline_for(collection, implementations, context):
    baseline = context.get("baseline." + collection, context.get("baseline", implementations[0]))
    if baseline not in implementations:
        raise ValueError("Missing comparison baseline: " + baseline)
    return baseline


def summarize(document, metadata):
    if metadata["repetitions"] < 3:
        raise ValueError("At least three repetitions are required")
    expected = set(metadata["expected_benchmarks"])
    if len(expected) != len(metadata["expected_benchmarks"]) or not expected:
        raise ValueError("Expected benchmark names must be nonempty and unique")
    context = document["context"]
    if context["build_type"] != "Release" or context["sanitizers"] != "0":
        raise ValueError("Performance reports require a Release build without sanitizers")
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
        collection, implementation, scenario, count = split_name(name)
        times = [repetitions[index] / count for index in sorted(repetitions)]
        implementations = groups.setdefault((collection, scenario, count), {})
        if implementation in implementations:
            raise ValueError("Duplicate comparison implementation: " + name)
        implementations[implementation] = {
            "median_ns_per_key": statistics.median(times),
            "cv_percent": statistics.stdev(times) / statistics.mean(times) * 100,
            "samples_ns_per_key": times,
        }
    rows = []
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    for (collection, scenario, count), implementations in sorted(groups.items()):
        expected_implementations = inventory[collection]
        if set(implementations) != set(expected_implementations):
            raise ValueError("Missing comparison implementation: " + scenario)
        baseline_name = baseline_for(collection, expected_implementations, context)
        baseline = implementations[baseline_name]["median_ns_per_key"]
        for implementation in expected_implementations:
            result = implementations[implementation]
            rows.append({
                "collection": collection,
                "scenario": scenario,
                "size": count,
                "implementation": implementation,
                **result,
                "baseline_implementation": baseline_name,
                "speedup_vs_baseline": baseline / result["median_ns_per_key"],
            })
    return rows


def validate_memory(document, context, expected_names):
    if document["schema_version"] not in (1, 2):
        raise ValueError("Unsupported memory-accounting schema")
    for key in MEMORY_CONTEXT_FIELDS:
        if document["context"][key] != context[key]:
            raise ValueError("CPU/memory configuration mismatch: " + key)
    for key, value in document["context"].items():
        if key not in context or context[key] != value:
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
        collection, implementation, scenario, size = split_name(name)
        if (row["before_heap_bytes"] + row["allocated_bytes"] - row["freed_bytes"]
                != row["end_heap_bytes"]):
            raise ValueError("Unbalanced memory bytes: " + name)
        if row["peak_heap_bytes"] < max(
                row["before_heap_bytes"], row["after_heap_bytes"], row["end_heap_bytes"]):
            raise ValueError("Invalid memory peak: " + name)
        if row["after_destroy_heap_bytes"] != 0:
            raise ValueError("Memory case leaked allocations: " + name)
        includes_destruction = row.get("includes_destruction")
        if document["schema_version"] == 2 and type(includes_destruction) is not bool:
            raise ValueError("Missing or invalid measurement boundary: " + name)
        if includes_destruction is True:
            if (row["before_heap_bytes"] != 0 or row["end_heap_bytes"] != 0
                    or row["allocations"] != row["deallocations"]):
                raise ValueError("Invalid lifecycle memory boundary: " + name)
        elif includes_destruction is False and row["end_heap_bytes"] != row["after_heap_bytes"]:
            raise ValueError("Invalid operation memory boundary: " + name)
        identity = (collection, implementation)
        if identity in object_sizes and object_sizes[identity] != row["object_bytes"]:
            raise ValueError("Inconsistent memory object size: " + name)
        object_sizes[identity] = row["object_bytes"]
        footprint = row["object_bytes"] + row["after_heap_bytes"]
        cases.append({
            **row, "collection": collection, "implementation": implementation,
            "scenario": scenario, "size": size,
            "footprint_bytes": footprint,
            "footprint_bytes_per_key": footprint / size,
            "peak_footprint_bytes": row["object_bytes"] + row["peak_heap_bytes"],
        })
    if seen != set(expected_names):
        raise ValueError("Missing memory cases")
    identities = {(row["collection"], row["implementation"], row["size"]) for row in cases}
    modes = document.get("lifecycle_modes")
    if document["schema_version"] == 1 and modes is None:
        modes = list(dict.fromkeys(row["mode"] for row in document["lifecycles"]))
    if (not isinstance(modes, list) or not modes or any(not isinstance(mode, str) or not mode for mode in modes)
            or len(modes) != len(set(modes))):
        raise ValueError("Missing or invalid lifecycle modes")
    expected_lifecycles = {
        (collection, implementation, mode, size)
        for collection, implementation, size in identities for mode in modes
    }
    seen_lifecycles = set()
    for row in document["lifecycles"]:
        collection = row.get("collection", "")
        key = (collection, row["implementation"], row["mode"], row["size"])
        if key not in expected_lifecycles or key in seen_lifecycles:
            raise ValueError("Duplicate or unexpected memory lifecycle")
        seen_lifecycles.add(key)
        fields = ("object_bytes", "empty_heap_bytes", "populated_heap_bytes",
                  "cleared_heap_bytes", "destroyed_heap_bytes")
        if any(type(row[field]) is not int or row[field] < 0 for field in fields):
            raise ValueError("Invalid memory lifecycle counters")
        if row["destroyed_heap_bytes"] != 0:
            raise ValueError("Memory lifecycle leaked allocations")
        if row["object_bytes"] != object_sizes[collection, row["implementation"]]:
            raise ValueError("Inconsistent lifecycle object size")
    if seen_lifecycles != expected_lifecycles:
        raise ValueError("Missing memory lifecycles")
    return {"cases": cases, "lifecycles": document["lifecycles"]}


def memory_markdown(cases, implementations):
    sizes = {}
    for row in cases:
        sizes.setdefault(row["size"], {})[row["implementation"]] = row
    lines = [
        "### Memory accounting",
        "",
        "Post-batch collection footprint: inline object bytes plus live requested heap bytes.",
        "This is a separate instrumented run; no instrumented timings are reported.",
        "",
        "| Keys | " + " | ".join(name + " B/key" for name in implementations) + " |",
        "|" + "---:|" * (len(implementations) + 1),
    ]
    for size, entries in sorted(sizes.items()):
        lowest = min(row["footprint_bytes_per_key"] for row in entries.values())
        cells = [str(size)]
        for name in implementations:
            value = entries[name]["footprint_bytes_per_key"]
            cell = f"{value:.3f}"
            cells.append(f"**{cell}**" if value == lowest else cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "<details>",
        "<summary>Memory details: exact bytes and allocation traffic</summary>",
        "",
        "| Keys | Measure | " + " | ".join(implementations) + " |",
        "|---:|---|" + "---:|" * len(implementations),
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
    for size, entries in sorted(sizes.items()):
        for key, label in metrics:
            cells = [str(size), label]
            cells += [f"{entries[name][key]:,}" for name in implementations]
            lines.append("| " + " | ".join(cells) + " |")
    return lines + ["", "</details>", ""]


def lifecycle_markdown(lifecycles, implementations):
    lines = [
        "## Lifecycle memory checkpoints",
        "",
        "Separate probes record empty, populated, cleared, and destroyed collections.",
        "Live-state footprints include inline object bytes; destroyed collections have zero footprint.",
        "",
    ]
    for mode in dict.fromkeys(row["mode"] for row in lifecycles):
        sizes = {}
        for row in lifecycles:
            if row["mode"] == mode:
                sizes.setdefault(row["size"], {})[row["implementation"]] = row
        lines += [
            f"### {mode}",
            "",
            "| Keys | State | " + " | ".join(name + " bytes" for name in implementations) + " |",
            "|---:|---|" + "---:|" * len(implementations),
        ]
        for size, entries in sorted(sizes.items()):
            for state in ("empty", "populated", "cleared", "destroyed"):
                cells = [str(size), state]
                for name in implementations:
                    row = entries[name]
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
        "Times are CPU ns/key. CV is sample standard deviation / mean.",
        "",
        "**Bold** marks the lowest median time or accounted footprint in each row (including ties).",
        "Speedup is relative to the artifact's baseline: above 1x is faster; below 1x is slower.",
        "[!] marks CV above 5%; exact values are in the variability details below.",
        "",
        f"Compiler: {context['compiler']}; STL: {context['stdlib']}; "
        f"target: {context['system']} {context['architecture']} "
        f"({context['pointer_bits']}-bit); runtime SIMD: {context['runtime_simd']}.",
        f"Repetitions: {metadata['repetitions']}; minimum CPU seconds/sample: {metadata['min_time']}.",
        "",
    ]
    details = {key: value for key, value in metadata.items()
               if value is None or isinstance(value, (str, bool, int, float))}
    if details:
        lines += ["| Run setting | Value |", "|---|---|"]
        lines += ["| " + key + " | " + str(value).replace("|", "\\|").replace("\n", "<br>") + " |"
                  for key, value in details.items()]
        lines.append("")
    for key, value in context.items():
        if key.startswith("note."):
            lines += [value, ""]
        elif key.startswith("warning."):
            lines += ["**Warning:** " + value, ""]
    if any(row["cv_percent"] > 5 for row in rows):
        lines += ["**Noise warning:** some measurements have CV above 5%; inspect raw samples.", ""]
    for warning in metadata.get("measurement_warnings", []):
        lines += [f"**Framework warning:** {warning}", ""]
    if memory is not None:
        lines += [
            "**Memory accounting:** performance and memory are separate runs of the same configuration.",
            "Footprint is sampled before destruction; peak includes pre-existing storage and transient growth.",
            "Each case records whether its allocation traffic includes destruction.",
            "",
        ]
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    for collection, implementation_names in inventory.items():
        baseline = baseline_for(collection, implementation_names, context)
        if collection:
            lines += [f"## {collection}", ""]
        description = context.get("collection." + collection)
        if description:
            lines += [description, ""]
        lines += [f"Baseline: `{baseline}`.", ""]
        scenarios = {}
        for row in rows:
            if row.get("collection", "") == collection:
                scenarios.setdefault(row["scenario"], {}).setdefault(row["size"], {})[
                    row["implementation"]
                ] = row
        for scenario, sizes in sorted(scenarios.items()):
            lines += [f"{'###' if collection else '##'} {scenario}", ""]
            description = context.get("scenario." + scenario)
            if description:
                lines += [description, ""]
            lines += [
                f"{'####' if collection else '###'} Performance",
                "",
                "| Keys | " + " | ".join(
                    name + " ns/key" + (" (speedup)" if name != baseline else "")
                    for name in implementation_names) + " |",
                "|" + "---:|" * (len(implementation_names) + 1),
            ]
            for size, implementations in sorted(sizes.items()):
                fastest = min(row["median_ns_per_key"] for row in implementations.values())
                cells = [str(size)]
                for implementation in implementation_names:
                    row = implementations[implementation]
                    cell = f"{row['median_ns_per_key']:.3f}"
                    if row["median_ns_per_key"] == fastest:
                        cell = f"**{cell}**"
                    if implementation != baseline:
                        cell += f" ({row['speedup_vs_baseline']:.2f}x)"
                    if row["cv_percent"] > 5:
                        cell += " [!]"
                    cells.append(cell)
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            if memory is not None:
                section = memory_markdown([
                    row for row in memory["cases"]
                    if row.get("collection", "") == collection and row["scenario"] == scenario
                ], implementation_names)
                lines += ["#" + line if collection and line.startswith("#") else line for line in section]
        if memory is not None:
            section = lifecycle_markdown([
                row for row in memory["lifecycles"] if row.get("collection", "") == collection
            ], implementation_names)
            lines += ["#" + line if collection and line.startswith("#") else line for line in section]
        lines += [
            "<details>",
            "<summary>Variability details (CV %)</summary>",
            "",
            "| Scenario | Keys | " + " | ".join(name + " CV %" for name in implementation_names) + " |",
            "|---|---:|" + "---:|" * len(implementation_names),
        ]
        for scenario, sizes in sorted(scenarios.items()):
            for size, implementations in sorted(sizes.items()):
                cells = [scenario, str(size)]
                cells += [f"{implementations[name]['cv_percent']:.2f}" for name in implementation_names]
                lines.append("| " + " | ".join(cells) + " |")
        lines += ["", "</details>", ""]
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
