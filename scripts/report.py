"""Validate complete repeated benchmark results and compare only within a run."""

import argparse
import csv
import hashlib
from html import escape
import io
import json
import math
from pathlib import Path
import re
import statistics


NANOSECONDS = {"ns": 1, "us": 1000, "ms": 1_000_000, "s": 1_000_000_000}
MEMORY_CONTEXT_FIELDS = (
    "compiler", "build_type", "system", "architecture", "pointer_bits", "runtime_simd",
    "dataset_seed", "stdlib", "sanitizers", "runtime_revision", "abseil_revision",
    "benchmark_revision",
)
HTML_STYLE = (
    "body{font:16px system-ui,sans-serif;color:#24292f;background:#f6f8fa;"
    "max-width:1100px;margin:32px auto;padding:0 20px}section{background:white;"
    "padding:20px;margin:24px 0;border:1px solid #d0d7de;border-radius:6px}"
    "svg{display:block;width:100%;height:auto}a{color:#0969da}"
    "table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 12px;"
    "border-bottom:1px solid #d0d7de}"
)
IMPLEMENTATION_NOTE = (
    "Legend: runtime = the .NET runtime implementation named in the collection description; "
    "std/std_deque = STL; abseil = Abseil. These are separate implementations, not aliases."
)


def display_description(text):
    # Historical producer text stays unchanged in raw artifacts.
    text = text.replace("native iterators", "each implementation's iterators")
    return re.sub(r"\bnative\b", lambda match: "Runtime" if match[0][0].isupper() else "runtime",
                  text, flags=re.IGNORECASE)


def scenario_descriptions(contexts):
    descriptions = {}
    for context in contexts:
        for key, value in context.items():
            if key.startswith("scenario."):
                entries = descriptions.setdefault(key.removeprefix("scenario."), [])
                text = display_description(value)
                if text not in entries:
                    entries.append(text)
    return {name: " ".join(entries) for name, entries in descriptions.items()}


def html_scenarios(contexts):
    descriptions = scenario_descriptions(contexts)
    if not descriptions:
        return ""
    parts = [
        "<section><h2>Scenarios measured</h2>",
        "<table><thead><tr><th>Scenario</th><th>Description</th></tr></thead><tbody>",
    ]
    parts += [f"<tr><td><code>{escape(name)}</code></td><td>{escape(text)}</td></tr>"
              for name, text in descriptions.items()]
    parts.append("</tbody></table>")
    for note in dict.fromkeys(display_description(context["note.scenarios"])
                              for context in contexts if "note.scenarios" in context):
        parts.append(f"<p>{escape(note)}</p>")
    return "\n".join(parts + ["</section>"])


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
    collection_modes = document.get("lifecycle_modes_by_collection", {})
    collections = {collection for collection, _, _ in identities}
    if (not isinstance(collection_modes, dict)
            or not set(collection_modes).issubset(collections)
            or any(not isinstance(names, list) or not names
                   or any(not isinstance(name, str) or not name for name in names)
                   or len(names) != len(set(names))
                   for names in collection_modes.values())):
        raise ValueError("Invalid per-collection lifecycle modes")
    expected_lifecycles = {
        (collection, implementation, mode, size)
        for collection, implementation, size in identities
        for mode in collection_modes.get(collection, modes)
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


def collection_graph(collection, rows, implementations, styles, memory=None):
    metrics = [("CPU median (ns/element)", "median_ns_per_key", rows)]
    if memory is not None:
        metrics.append(("Collection footprint (B/element)", "footprint_bytes_per_key", memory))
    scenarios = sorted({row["scenario"] for row in rows})
    width, panel_width, panel_height = 40 + 500 * len(metrics), 500, 225
    legend = []
    x, y = 30, 57
    for name in implementations:
        space = 48 + len(name) * 8
        if x + space > width - 30:
            x, y = 30, y + 24
        color, dash = styles[name]
        legend += [
            f'<line x1="{x}" y1="{y - 4}" x2="{x + 24}" y2="{y - 4}" '
            f'stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"/>',
            f'<text x="{x + 30}" y="{y}">{escape(name)}</text>',
        ]
        x += space
    top = y + 35
    height = top + len(scenarios) * panel_height + 20
    title = f"{collection or 'Collections'}: CPU and memory comparisons" if memory is not None else (
        f"{collection or 'Collections'}: CPU comparisons")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title, quote=True)}">',
        f'<title>{escape(title)}</title>',
        '<desc>Implementations compared for every test and measured size. '
        'Lower is better. Each panel uses an independent linear vertical scale. '
        'Element counts are equally spaced categories, not a linear horizontal scale.</desc>',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        '<g font-family="sans-serif" font-size="12" fill="#24292f">',
        f'<text x="30" y="27" font-size="18" font-weight="bold">{escape(collection or "Collections")}</text>',
        *legend,
    ]
    for index, scenario in enumerate(scenarios):
        for column, (label, metric, entries) in enumerate(metrics):
            left = 85 + column * panel_width
            plot_top = top + index * panel_height + 45
            plot_width, plot_height = 400, 125
            values = [row for row in entries if row["scenario"] == scenario]
            sizes = sorted({row["size"] for row in values})
            maximum = max(row[metric] for row in values) * 1.1 or 1
            parts.append(
                f'<text x="{left}" y="{plot_top - 28}" font-weight="bold">'
                f'{escape(scenario)}: {escape(label)}</text>')
            parts.append(
                f'<text x="{left}" y="{plot_top - 12}" fill="#57606a">Lower is better</text>')
            for tick in range(4):
                value = maximum * tick / 3
                tick_y = plot_top + plot_height * (1 - tick / 3)
                parts += [
                    f'<line x1="{left}" y1="{tick_y:.2f}" x2="{left + plot_width}" '
                    f'y2="{tick_y:.2f}" stroke="#d0d7de"/>',
                    f'<text x="{left - 8}" y="{tick_y + 4:.2f}" text-anchor="end">{value:.3g}</text>',
                ]
            positions = {
                size: left + plot_width * (i / (len(sizes) - 1) if len(sizes) > 1 else 0.5)
                for i, size in enumerate(sizes)
            }
            for size, position in positions.items():
                parts.append(
                    f'<text x="{position:.2f}" y="{plot_top + plot_height + 20}" '
                    f'text-anchor="middle">{size:,}</text>')
            parts.append(
                f'<text x="{left + plot_width / 2}" y="{plot_top + plot_height + 39}" '
                'text-anchor="middle">Element count (equally spaced sizes)</text>')
            for name in implementations:
                series = sorted((row for row in values if row["implementation"] == name),
                                key=lambda row: row["size"])
                color, dash = styles[name]
                points = [(positions[row["size"]],
                           plot_top + plot_height * (1 - row[metric] / maximum)) for row in series]
                coordinates = " ".join(f"{px:.2f},{py:.2f}" for px, py in points)
                parts.append(
                    f'<polyline points="{coordinates}" fill="none" stroke="{color}" '
                    f'stroke-width="2" stroke-dasharray="{dash}"/>')
                for row, (px, py) in zip(series, points):
                    description = f'{name}; {scenario}; {row["size"]} elements; {row[metric]} {label}'
                    if metric == "median_ns_per_key":
                        description += f'; CV {row["cv_percent"]:.2f}%'
                    parts.append(
                        f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.5" fill="{color}" '
                        f'data-implementation="{escape(name, quote=True)}" data-size="{row["size"]}" '
                        f'data-metric="{metric}" data-value="{row[metric]}">'
                        f'<title>{escape(description)}</title></circle>')
            for i, size in enumerate(sizes):
                start = left if i == 0 else (positions[sizes[i - 1]] + positions[size]) / 2
                end = left + plot_width if i == len(sizes) - 1 else (
                    positions[size] + positions[sizes[i + 1]]) / 2
                tooltip = [f"{scenario}: {size:,} elements", label]
                by_name = {row["implementation"]: row for row in values if row["size"] == size}
                for name in implementations:
                    row = by_name[name]
                    detail = f"{name}: {row[metric]}"
                    if metric == "median_ns_per_key":
                        detail += (f'; {row["speedup_vs_baseline"]:.3f}x vs '
                                   f'{row["baseline_implementation"]}; CV {row["cv_percent"]:.2f}%')
                    else:
                        detail += f'; total footprint {row["footprint_bytes"]:,} bytes'
                    tooltip.append(detail)
                parts.append(
                    f'<rect x="{start:.2f}" y="{plot_top}" width="{end - start:.2f}" '
                    f'height="{plot_height}" fill="transparent" data-tooltip="true">'
                    f'<title>{escape(chr(10).join(tooltip))}</title></rect>')
    return "\n".join(parts + ["</g>", "</svg>", ""])


def summary_notes(rows, context, metadata, memory):
    notes = [
        "Compare implementations within this run, not across machines. "
        "These measurements inform, but do not decide, whether to keep, improve, or replace a collection.",
        IMPLEMENTATION_NOTE,
        f"Compiler: {context['compiler']}; STL: {context['stdlib']}; "
        f"target: {context['system']} {context['architecture']} "
        f"({context['pointer_bits']}-bit); runtime SIMD: {context['runtime_simd']}.",
        f"Repetitions: {metadata['repetitions']}; minimum CPU seconds/sample: {metadata['min_time']}.",
        "Lower is better. CPU graphs show median ns/element. Each panel uses an independent linear "
        "vertical scale; measured element counts are equally spaced horizontal categories. "
        "No workloads or collection sizes are averaged together.",
    ]
    if memory is not None:
        notes.append(
            "Memory graphs show post-batch object-plus-live-heap B/element, measured separately "
            "from CPU timing. No instrumented timings are reported. Allocation traffic, peak "
            "footprints, and lifecycle checkpoints remain in the CSV/JSON exports.")
    if metadata.get("functional_tests") == "not_run":
        notes.append("Functional validation was not run for these measurements.")
    for key, value in context.items():
        if key.startswith("note.") and key != "note.scenarios":
            notes.append(display_description(value))
        elif key.startswith("warning."):
            notes.append("Warning: " + display_description(value))
    if any(row["cv_percent"] > 5 for row in rows):
        notes.append("Noise warning: some measurements have CV above 5%; inspect tooltips and raw samples.")
    for warning in metadata.get("measurement_warnings", []):
        notes.append("Framework warning: " + warning)
    return notes


def markdown(rows, context, metadata, memory=None):
    lines = [
        "# Collection benchmark results",
        "",
        "[Open the interactive HTML summary](summary.html) for hover tooltips with all implementation "
        "values at each measured size, CPU speedups, and variability (CV). "
        "Markdown image previews may not expose tooltips.",
        "",
    ]
    notes = summary_notes(rows, context, metadata, memory)
    for note in notes[:2]:
        lines += [note, ""]
    descriptions = scenario_descriptions([context])
    if descriptions:
        lines += ["## Scenarios measured", "", "| Scenario | Description |", "| --- | --- |"]
        for name, text in descriptions.items():
            lines.append("| " + name.replace("|", "&#124;") + " | "
                         + text.replace("|", "&#124;").replace("\n", " ") + " |")
        lines.append("")
        if "note.scenarios" in context:
            lines += [display_description(context["note.scenarios"]), ""]
    for note in notes[2:]:
        lines += [note, ""]
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    for index, (collection, implementations) in enumerate(inventory.items(), 1):
        label = collection or "Collections"
        lines += [f"## {label}", ""]
        description = context.get("collection." + collection)
        if description:
            lines += [display_description(description), ""]
        lines += [
            f"Baseline for tooltip speedups: `{baseline_for(collection, implementations, context)}`.",
            "",
            f"![{label} comparisons](graphs/collection-{index}.svg)",
            "",
        ]
    lines += [
        "## Data exports", "",
        "CPU medians, speedups, CV, and samples: [CSV](summary.csv), [JSON](summary.json).", "",
    ]
    if memory is not None:
        lines += [
            "Memory footprints and allocation traffic: [CSV](memory-summary.csv), "
            "[JSON](memory-summary.json).", "",
            "Lifecycle checkpoints: [CSV](memory-lifecycle.csv).", "",
        ]
    return "\n".join(lines) + "\n"


def html_collections(context, metadata, graphs, prefix=""):
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    parts = []
    for index, (collection, graph) in enumerate(graphs, 1):
        parts += [
            f'<section id="{escape(prefix)}collection-{index}"><h2>{escape(collection or "Collections")}</h2>',
            f"<p>{escape(display_description(context.get('collection.' + collection, '')))}</p>",
            "<p>Baseline for tooltip speedups: "
            f"<code>{escape(baseline_for(collection, inventory[collection], context))}</code>.</p>",
            graph, "</section>",
        ]
    return "\n".join(parts)


def html_summary(rows, context, metadata, memory, graphs):
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>Collection benchmark results</title>',
        f'<style>{HTML_STYLE}</style></head><body>',
        '<h1>Collection benchmark results</h1>',
        f"<p>{escape(IMPLEMENTATION_NOTE)}</p>",
        html_scenarios([context]),
        '<nav aria-labelledby="collection-index"><h2 id="collection-index">Collections</h2>',
        '<table><thead><tr><th>Collection</th><th>Implementations</th></tr></thead><tbody>',
    ]
    for index, (collection, _) in enumerate(graphs, 1):
        parts.append(
            f'<tr><td><a href="#collection-{index}">{escape(collection or "Collections")}</a></td>'
            f'<td>{escape(", ".join(inventory[collection]))}</td></tr>')
    parts += [
        "</tbody></table></nav>",
        '<p>Hover over a measured size in a panel to see every implementation, including overlapping '
        'points. CPU tooltips include speedup versus baseline (above 1x is faster) and CV '
        '(sample standard deviation / mean).</p>',
    ]
    parts += [f"<p>{escape(note)}</p>" for note in summary_notes(rows, context, metadata, memory)
              if note != IMPLEMENTATION_NOTE]
    parts.append(html_collections(context, metadata, graphs))
    parts += [
        '<p>CPU data: <a href="summary.csv">CSV</a>, <a href="summary.json">JSON</a>.</p>',
    ]
    if memory is not None:
        parts.append(
            '<p>Memory data: <a href="memory-summary.csv">CSV</a>, '
            '<a href="memory-summary.json">JSON</a>; '
            '<a href="memory-lifecycle.csv">lifecycle checkpoints</a>.</p>')
    return "\n".join(parts + ["</body></html>", ""])


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
    graph_directory = directory / "graphs"
    graph_directory.mkdir(exist_ok=True)
    inventory = comparison_inventory(metadata["expected_benchmarks"])
    names = list(dict.fromkeys(name for implementations in inventory.values() for name in implementations))
    colors = ("#0072b2", "#d55e00", "#009e73", "#cc79a7", "#806600", "#56b4e9", "#24292f")
    dashes = ("none", "7 3", "2 3", "9 3 2 3")
    styles = {name: (colors[index % len(colors)], dashes[index % len(dashes)])
              for index, name in enumerate(names)}
    graphs = []
    for index, (collection, implementations) in enumerate(inventory.items(), 1):
        graph = collection_graph(
            collection, [row for row in rows if row["collection"] == collection],
            implementations, styles,
            [row for row in memory["cases"] if row["collection"] == collection]
            if memory is not None else None)
        (graph_directory / f"collection-{index}.svg").write_text(graph)
        graphs.append((collection, graph))
    (directory / "summary.html").write_text(
        html_summary(rows, document["context"], metadata, memory, graphs))
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
