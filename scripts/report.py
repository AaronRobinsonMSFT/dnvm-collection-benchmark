"""Validate complete repeated benchmark results and compare only within a run."""

import argparse
import csv
import io
import json
import math
from pathlib import Path
import statistics


IMPLEMENTATIONS = ("runtime", "std", "abseil")
NANOSECONDS = {"ns": 1, "us": 1000, "ms": 1_000_000, "s": 1_000_000_000}


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


def markdown(rows, context, metadata):
    lines = [
        "# Collection benchmark results",
        "",
        "Exploratory measurements, not a replacement recommendation.",
        "Ratios compare implementations in this run only; higher speedup is faster.",
        "Times are CPU ns/key. Build scenarios include construction, reservation, and destruction;",
        "erase_insert includes both operations per key; clear_refill includes clearing and refilling.",
        "Default hashes and allocation policies differ. CV is sample standard deviation / mean.",
        "",
        f"Compiler: {context['compiler']}; STL: {context['stdlib']}; "
        f"target: {context['system']} {context['architecture']} "
        f"({context['pointer_bits']}-bit); runtime SIMD: {context['runtime_simd']}.",
        f"Repetitions: {metadata['repetitions']}; minimum CPU seconds/sample: {metadata['min_time']}.",
        "",
    ]
    if metadata["known_duplicate_defect_reproduced"]:
        lines += [
            "**Known functional defect:** the separate duplicate-after-deletion reproducer",
            "detected an existing key being inserted again after deletion.",
            "",
        ]
    if any(row["cv_percent"] > 5 for row in rows):
        lines += ["**Noise warning:** some measurements have CV above 5%; inspect raw samples.", ""]
    for warning in metadata.get("measurement_warnings", []):
        lines += [f"**Framework warning:** {warning}", ""]
    lines += [
        "| Scenario | Keys | Implementation | Median ns/key | CV % | Speedup vs runtime |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['size']} | {row['implementation']} | "
            f"{row['median_ns_per_key']:.3f} | {row['cv_percent']:.2f} | "
            f"{row['speedup_vs_runtime']:.2f}x |"
        )
    return "\n".join(lines) + "\n"


def write_reports(directory):
    document = json.loads((directory / "benchmarks.json").read_text())
    metadata = json.loads((directory / "metadata.json").read_text())
    rows = summarize(document, metadata)
    (directory / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    output = io.StringIO(newline="")
    fields = [key for key in rows[0] if key != "samples_ns_per_key"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    (directory / "summary.csv").write_text(output.getvalue())
    (directory / "summary.md").write_text(markdown(rows, document["context"], metadata))
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
