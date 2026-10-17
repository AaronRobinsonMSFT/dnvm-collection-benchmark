"""Combine platform artifacts and functional results from one Collections run."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from report import write_reports


PLATFORMS = {
    "linux-gcc-x64": ("Linux", "GNU", "64", ("x86_64", "amd64"), "sse2"),
    "linux-clang-x64": ("Linux", "Clang", "64", ("x86_64", "amd64"), "sse2"),
    "macos-arm64": ("Darwin", "AppleClang", "64", ("arm64", "aarch64"), "neon"),
    "windows-x64": ("Windows", "MSVC", "64", ("x64",), "sse2"),
    "windows-x86": ("Windows", "MSVC", "32", ("x86",), "sse2"),
}
VALIDATION_PLATFORMS = ("linux-gcc-x64", "linux-clang-x64")


def read_validation(directory, commit):
    if (directory / "commit.txt").read_text().strip() != commit:
        raise ValueError("Functional results belong to a different commit")
    cases = list(ET.parse(directory / "functional-tests.xml").getroot().iter("testcase"))
    names = [case.attrib["name"] for case in cases]
    if len(names) != 3 or set(names) != {"map_contract", "memory_accounting", "report_contract"}:
        raise ValueError("Missing or duplicate functional test groups")
    if any(case.find(tag) is not None
           for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("Functional tests failed or were skipped")


def read_platform(directory, name, commit):
    metadata = json.loads((directory / "metadata.json").read_text())
    memory_metadata = json.loads((directory / "memory-metadata.json").read_text())
    for item in (metadata, memory_metadata):
        if item["project_commit"] != commit or item["project_status"]:
            raise ValueError("Measurements must use the expected commit and a clean working tree")
        if item["functional_tests"] != "not_run":
            raise ValueError("Data collection must not run the functional suite")
    document = json.loads((directory / "benchmarks.json").read_text())
    context = document["context"]
    system, compiler, bits, architectures, simd = PLATFORMS[name]
    if (context["system"] != system or context["compiler"].split()[0] != compiler
            or context["pointer_bits"] != bits
            or context["architecture"].lower() not in architectures
            or context["runtime_simd"] != simd):
        raise ValueError("Measurements do not match the requested platform")
    if not (directory / "memory.json").is_file():
        raise ValueError("Missing memory measurements")
    write_reports(directory)
    return {
        "platform": name,
        "context": context,
        "metadata": metadata,
        "cpu": json.loads((directory / "summary.json").read_text()),
        "memory": json.loads((directory / "memory-summary.json").read_text()),
    }


def write_csv(path, rows):
    fields = [key for key in rows[0] if key != "samples_ns_per_key"]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def combine(directory, commit, collection_status="success"):
    directory.mkdir(parents=True, exist_ok=True)
    result = {"commit": commit, "functional": {}, "platforms": [], "errors": []}
    if collection_status != "success":
        result["errors"].append("Collection jobs: " + collection_status)
    for name in VALIDATION_PLATFORMS:
        try:
            read_validation(directory / ("validation-" + name), commit)
            result["functional"][name] = "passed"
        except (OSError, ValueError, KeyError, ET.ParseError) as error:
            result["functional"][name] = "failed"
            result["errors"].append(name + " validation: " + str(error))
    baseline = None
    for name in PLATFORMS:
        try:
            platform = read_platform(directory / ("results-" + name), name, commit)
            metadata = platform["metadata"]
            comparison = {
                "dependencies": metadata["dependencies"],
                "benchmarks": sorted(metadata["expected_benchmarks"]),
                "repetitions": metadata["repetitions"],
                "min_time": metadata["min_time"],
                "dataset_seed": platform["context"]["dataset_seed"],
            }
            if baseline is not None and comparison != baseline:
                raise ValueError("Dependency revisions, datasets, or measurement coverage differ")
            baseline = comparison
            result["platforms"].append(platform)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["errors"].append(name + ": " + str(error))
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    cpu = []
    memory = []
    lifecycles = []
    for platform in result["platforms"]:
        name = platform["platform"]
        cpu += [{"platform": name, **row} for row in platform["cpu"]]
        memory += [{"platform": name, **row} for row in platform["memory"]["cases"]]
        lifecycles += [{"platform": name, **row} for row in platform["memory"]["lifecycles"]]
    if cpu:
        write_csv(directory / "summary.csv", cpu)
        write_csv(directory / "memory-summary.csv", memory)
        write_csv(directory / "memory-lifecycle.csv", lifecycles)
    else:
        for name in ("summary.csv", "memory-summary.csv", "memory-lifecycle.csv"):
            (directory / name).unlink(missing_ok=True)
    lines = [
        "# Collection results",
        "",
        f"Commit: `{commit}`",
        "",
        "## Functional validation",
        "",
        "| Linux compiler | Status |",
        "|---|---|",
        *[f"| {name} | {status} |" for name, status in result["functional"].items()],
        "",
        "## CPU and memory collection",
        "",
        "| Platform | Status | CPU comparisons | Memory scenarios |",
        "|---|---|---:|---:|",
    ]
    complete = {platform["platform"]: platform for platform in result["platforms"]}
    for name in PLATFORMS:
        if name in complete:
            platform = complete[name]
            lines.append(f"| {name} | complete | {len(platform['cpu'])} | "
                         f"{len(platform['memory']['cases'])} |")
        else:
            lines.append(f"| {name} | missing or invalid | - | - |")
    lines += [
        "",
        "Ratios compare implementations within each platform run, not across machines.",
        "Download `collection-results` for the reports, CSV/JSON, raw data, and logs.",
        "",
    ]
    if result["errors"]:
        lines += ["## Errors", "", *["- " + error for error in result["errors"]], ""]
    for name in complete:
        lines += [
            "<details>",
            f"<summary>{name}</summary>",
            "",
            (directory / ("results-" + name) / "summary.md").read_text(),
            "</details>",
            "",
        ]
    (directory / "summary.md").write_text("\n".join(lines))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--collection-status", default="success")
    args = parser.parse_args()
    try:
        result = combine(args.artifacts, args.commit, args.collection_status)
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
                summary.write((args.artifacts / "summary.md").read_text())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Combined report failed: {error}\n")
    if result["errors"]:
        parser.exit(1, "Combined report incomplete; see summary.md for errors\n")
    print(f"Combined {len(result['platforms'])} platform reports: {args.artifacts / 'summary.md'}")


if __name__ == "__main__":
    main()
