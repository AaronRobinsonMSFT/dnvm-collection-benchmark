"""Combine platform artifacts and functional results from one Collections run."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from report import write_reports


def read_validation(directory, commit):
    if (directory / "commit.txt").read_text().strip() != commit:
        raise ValueError("Functional results belong to a different commit")
    root = ET.parse(directory / "functional-tests.xml").getroot()
    suites = [suite for suite in root.iter("testsuite") if not suite.findall("testsuite")]
    cases = [case for suite in suites for case in suite.findall("testcase")]
    identities = [(suite.attrib.get("name", ""), case.attrib.get("classname", ""), case.attrib["name"])
                  for suite in suites for case in suite.findall("testcase")]
    if (not cases or len(identities) != len(set(identities))
            or any(int(suite.attrib["tests"]) != len(suite.findall("testcase")) for suite in suites)):
        raise ValueError("Missing or duplicate functional test results")
    if any(int(suite.attrib.get(field, 0)) != 0
           for suite in suites for field in ("failures", "errors", "skipped", "disabled")):
        raise ValueError("Functional tests failed or were skipped")
    if any(case.find(tag) is not None
           for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("Functional tests failed or were skipped")


def read_platform(directory, name, commit):
    metadata = json.loads((directory / "metadata.json").read_text())
    memory_metadata = json.loads((directory / "memory-metadata.json").read_text())
    for item in (metadata, memory_metadata):
        if item["project_commit"] != commit or item["project_status"]:
            raise ValueError("Measurements must use the expected commit and a clean working tree")
    document = json.loads((directory / "benchmarks.json").read_text())
    context = document["context"]
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
    runs = {}
    expected_counts = set()
    used_directories = set()
    for path in sorted(directory.glob("run-*.json")):
        try:
            run = json.loads(path.read_text())
            name = run["name"]
            if (run["schema_version"] != 1 or run["commit"] != commit
                    or not isinstance(name, str) or not name or name in runs
                    or type(run["expected_runs"]) is not int or run["expected_runs"] <= 0):
                raise ValueError("Invalid, duplicate, or mismatched run inventory")
            for key in ("measurements", "validation"):
                value = run[key]
                if key == "validation" and value is None:
                    continue
                if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
                    raise ValueError("Run inventory must reference child artifact directories")
            paths = {value for key, value in run.items() if key in ("measurements", "validation") and value is not None}
            if paths & used_directories or run["measurements"] == run["validation"]:
                raise ValueError("Run inventory reuses an artifact directory")
            runs[name] = run
            used_directories.update(paths)
            expected_counts.add(run["expected_runs"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["errors"].append(path.name + ": " + str(error))
    if not runs or expected_counts != {len(runs)}:
        result["errors"].append(
            f"Incomplete run inventory: found {len(runs)}, declared counts {sorted(expected_counts)}")
    baseline = None
    for name, run in runs.items():
        if run["validation"] is not None:
            try:
                read_validation(directory / run["validation"], commit)
                result["functional"][name] = "passed"
            except (OSError, ValueError, KeyError, ET.ParseError) as error:
                result["functional"][name] = "failed"
                result["errors"].append(name + " validation: " + str(error))
        try:
            platform = read_platform(directory / run["measurements"], name, commit)
            metadata = platform["metadata"]
            comparison = {
                "dependencies": metadata["dependencies"],
                "benchmarks": sorted(metadata["expected_benchmarks"]),
                "repetitions": metadata["repetitions"],
                "min_time": metadata["min_time"],
                "dataset_seed": platform["context"]["dataset_seed"],
                "descriptions": {key: value for key, value in platform["context"].items()
                                 if key == "baseline" or key.startswith(("baseline.", "collection.", "note."))},
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
        "| Validation run | Status |",
        "|---|---|",
        *[f"| {name} | {status} |" for name, status in result["functional"].items()],
        "",
        "## CPU and memory collection",
        "",
        "| Platform | Status | CPU comparisons | Memory scenarios |",
        "|---|---|---:|---:|",
    ]
    complete = {platform["platform"]: platform for platform in result["platforms"]}
    for name in runs:
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
            (directory / runs[name]["measurements"] / "summary.md").read_text(),
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
