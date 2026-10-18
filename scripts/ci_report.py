"""Combine platform artifacts from one Collections run."""

import argparse
import csv
from html import escape
import json
import os
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

from report import HTML_STYLE, comparison_inventory, html_collections, summary_notes, write_reports


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


def read_executive_summary(path, commit):
    notes = json.loads(path.read_text())
    if not isinstance(notes, dict) or notes.get("commit") != commit:
        raise ValueError("Executive summary belongs to a different commit")
    if not isinstance(notes.get("methodology", ""), str):
        raise ValueError("Invalid executive-summary methodology")
    for field, heading in (("findings", "finding"), ("action_items", "action")):
        entries = notes.get(field)
        if (not isinstance(entries, list)
                or any(not isinstance(entry, dict)
                       or not isinstance(entry.get(heading), str)
                       or not isinstance(entry.get("detail"), str) for entry in entries)):
            raise ValueError("Invalid executive-summary " + field)
    return notes


def html_summary(directory, result, runs, notes):
    platforms = {platform["platform"]: platform for platform in result["platforms"]}
    indices = {
        name: {collection: index for index, collection in enumerate(
            comparison_inventory(platform["metadata"]["expected_benchmarks"]), 1)}
        for name, platform in platforms.items()
    }
    collections = list(dict.fromkeys(collection for inventory in indices.values() for collection in inventory))
    prefixes = {name: f"platform-{index}-" for index, name in enumerate(runs, 1)}
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>All-platform collection results</title>',
        f'<style>{HTML_STYLE}.collection-index{{overflow-x:auto}}td{{vertical-align:top}}'
        'li{margin:12px 0}</style></head><body>',
        '<h1>All-platform collection results</h1>',
        '<p>Compare implementations within each platform run, not across machines. '
        'CPU and memory measurements remain separate; no workloads or platforms are averaged together.</p>',
    ]
    if result["errors"]:
        parts += ['<h2>Errors</h2><ul>']
        parts += [f"<li>{escape(error)}</li>" for error in result["errors"]]
        parts.append("</ul>")
    parts += [
        '<nav class="collection-index" aria-labelledby="collection-index">'
        '<h2 id="collection-index">Jump to graphs</h2>',
        '<table><thead><tr><th>Collection</th>',
    ]
    parts += [f"<th>{escape(name)}</th>" for name in runs]
    parts.append("</tr></thead><tbody>")
    for collection in collections:
        parts.append(f"<tr><th>{escape(collection or 'Collections')}</th>")
        for name in runs:
            if collection in indices.get(name, {}):
                target = prefixes[name] + "collection-" + str(indices[name][collection])
                parts.append(f'<td><a href="#{target}">Graphs</a></td>')
            else:
                parts.append("<td>Unavailable</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></nav>")
    if notes is not None:
        if notes.get("methodology"):
            parts.append(f"<p>{escape(notes['methodology'])}</p>")
        for field, heading, title in (
                ("findings", "finding", "Executive summary"),
                ("action_items", "action", "Action items")):
            parts.append(f"<section><h2>{title}</h2><ul>")
            parts += [f"<li><strong>{escape(entry[heading])}.</strong> {escape(entry['detail'])}</li>"
                      for entry in notes[field]]
            parts.append("</ul></section>")
    parts += [
        '<section><h2>Platform coverage and variability</h2>',
        '<p>Hover over a measured size to see every implementation, exact values, and CV '
        '(sample standard deviation / mean). Zero CV does not establish timer precision.</p>',
        '<table><thead><tr><th>Platform</th><th>Status</th><th>CPU configurations</th>'
        '<th>Memory cases</th><th>Median CV</th><th>CV &gt;10%</th><th>Zero CV</th></tr></thead><tbody>',
    ]
    for name in runs:
        if name not in platforms:
            parts.append(f"<tr><th>{escape(name)}</th><td>Missing or invalid</td>"
                         "<td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>")
            continue
        platform = platforms[name]
        cvs = [row["cv_percent"] for row in platform["cpu"]]
        measurement = escape(runs[name]["measurements"])
        parts.append(
            f'<tr><th><a href="{measurement}/summary.html">{escape(name)}</a></th>'
            f'<td>Complete</td><td>{len(cvs)}</td><td>{len(platform["memory"]["cases"])}</td>'
            f'<td>{statistics.median(cvs):.1f}%</td>'
            f'<td>{sum(cv > 10 for cv in cvs)}/{len(cvs)}</td>'
            f'<td>{sum(cv == 0 for cv in cvs)}/{len(cvs)}</td></tr>')
    parts.append("</tbody></table></section>")
    if result["functional"]:
        parts += ["<h2>Functional validation</h2><ul>"]
        parts += [f"<li>{escape(name)}: {escape(status)}</li>"
                  for name, status in result["functional"].items()]
        parts.append("</ul>")
    for name, platform in platforms.items():
        context, metadata = platform["context"], platform["metadata"]
        measurement = runs[name]["measurements"]
        parts += [
            f'<section><h2>{escape(name)}</h2>',
            '<p><a href="#collection-index">Back to all-platform graph index</a></p>',
        ]
        parts += [f"<p>{escape(note)}</p>" for note in summary_notes(
            platform["cpu"], context, metadata, platform["memory"])]
        graphs = [(collection, (directory / measurement / "graphs" / f"collection-{index}.svg").read_text())
                  for collection, index in indices[name].items()]
        parts.append(html_collections(context, metadata, graphs, prefixes[name]))
        parts.append(f'<p><a href="{escape(measurement)}/summary.html">Separate platform report</a></p></section>')
    parts.append('<p>Combined data: <a href="summary.json">JSON</a>')
    if platforms:
        parts.append(', <a href="summary.csv">CPU CSV</a>, '
                     '<a href="memory-summary.csv">memory CSV</a>, '
                     '<a href="memory-lifecycle.csv">lifecycle CSV</a>')
    if notes is not None:
        parts.append(', <a href="executive-summary.json">executive-summary input</a>')
    parts += [".</p>", "</body></html>", ""]
    return "\n".join(parts)


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
                value = run.get(key) if key == "validation" else run[key]
                if key == "validation" and value is None:
                    continue
                if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
                    raise ValueError("Run inventory must reference child artifact directories")
            paths = {value for key, value in run.items() if key in ("measurements", "validation") and value is not None}
            if paths & used_directories or run["measurements"] == run.get("validation"):
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
        if run.get("validation") is not None:
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
    notes = None
    notes_path = directory / "executive-summary.json"
    if notes_path.exists():
        try:
            notes = read_executive_summary(notes_path, commit)
        except (OSError, ValueError) as error:
            result["errors"].append(notes_path.name + ": " + str(error))
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
        "[Open the interactive all-platform summary](summary.html) for graph navigation and hover values.",
        "",
        f"Commit: `{commit}`",
        "",
    ]
    if result["functional"]:
        lines += [
            "## Functional validation",
            "",
            "| Validation run | Status |",
            "|---|---|",
            *[f"| {name} | {status} |" for name, status in result["functional"].items()],
            "",
        ]
    lines += [
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
        report = (directory / runs[name]["measurements"] / "summary.md").read_text()
        for prefix in ("graphs/", "summary.", "memory-summary.", "memory-lifecycle."):
            report = report.replace("](" + prefix, "](" + runs[name]["measurements"] + "/" + prefix)
        lines += [
            "<details>",
            f"<summary>{name}</summary>",
            "",
            report,
            "</details>",
            "",
        ]
    (directory / "summary.md").write_text("\n".join(lines))
    (directory / "summary.html").write_text(html_summary(directory, result, runs, notes))
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
    print(f"Combined {len(result['platforms'])} platform reports: {args.artifacts / 'summary.html'}")


if __name__ == "__main__":
    main()
