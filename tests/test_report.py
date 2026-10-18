import copy
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SPEC = importlib.util.spec_from_file_location(
    "report", Path(__file__).resolve().parents[1] / "scripts" / "report.py"
)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)

IMPLEMENTATIONS = ("runtime", "std", "abseil")
COLLECTIONS = ("numbers", "addresses", "pairs", "text")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "repetitions": 3,
            "expected_benchmarks": [name + "/lookup_hit/8" for name in IMPLEMENTATIONS],
            "dependencies": {"runtime": {"commit": "abc", "dirty": False}},
            "min_time": 0.1,
            "known_duplicate_defect_reproduced": False,
        }
        self.document = {
            "context": {
                "build_type": "Release", "runtime_simd": "neon",
                "sanitizers": "0", "runtime_revision": "abc",
                "compiler": "test", "stdlib": "test", "system": "Darwin",
                "architecture": "arm64", "pointer_bits": "64",
                "dataset_seed": "123", "abseil_revision": "def", "benchmark_revision": "ghi",
                "baseline": "runtime",
                "warning.fixture": "Known functional defect: illustrative artifact warning.",
            },
            "benchmarks": [],
        }
        for implementation, time in (("runtime", 80), ("std", 160), ("abseil", 40)):
            for index in range(3):
                self.document["benchmarks"].append({
                    "name": implementation + "/lookup_hit/8",
                    "run_type": "iteration", "repetition_index": index,
                    "cpu_time": time, "time_unit": "ns", "iterations": 100,
                })

    def test_normalization_and_ratio_direction(self):
        rows = REPORT.summarize(self.document, self.metadata)
        self.assertEqual([row["median_ns_per_key"] for row in rows], [10, 20, 5])
        self.assertEqual([row["speedup_vs_baseline"] for row in rows], [1, 0.5, 2])

    def test_rejects_incomplete_invalid_or_failed_samples(self):
        cases = []
        missing = copy.deepcopy(self.document)
        missing["benchmarks"].pop()
        cases.append(missing)
        duplicate = copy.deepcopy(self.document)
        duplicate["benchmarks"].append(copy.deepcopy(duplicate["benchmarks"][0]))
        cases.append(duplicate)
        for change in ({"cpu_time": float("nan")}, {"cpu_time": 0},
                       {"error_occurred": True}, {"iterations": 0},
                       {"repetition_index": 9}, {"name": "unexpected/lookup_hit/8"}):
            document = copy.deepcopy(self.document)
            document["benchmarks"][0].update(change)
            cases.append(document)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ValueError):
                REPORT.summarize(document, self.metadata)

    def test_inventory_can_declare_a_different_number_of_implementations(self):
        self.metadata["expected_benchmarks"] = self.metadata["expected_benchmarks"][:-1]
        self.document["benchmarks"] = self.document["benchmarks"][:-3]
        rows = REPORT.summarize(self.document, self.metadata)
        self.assertEqual([row["implementation"] for row in rows], ["runtime", "std"])
        self.assertEqual([row["speedup_vs_baseline"] for row in rows], [1, 0.5])

    def test_rejects_unrepresentative_or_mismatched_build(self):
        for key, value in (("build_type", "Debug"), ("sanitizers", "1"),
                           ("runtime_revision", "wrong")):
            document = copy.deepcopy(self.document)
            document["context"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                REPORT.summarize(document, self.metadata)
        self.metadata["dependencies"]["runtime"]["dirty"] = True
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            REPORT.summarize(self.document, self.metadata)

    def test_normalizes_units_and_ignores_aggregate_rows(self):
        self.document["benchmarks"][0]["cpu_time"] = 0.08
        self.document["benchmarks"][0]["time_unit"] = "us"
        self.document["benchmarks"].append({"name": "summary", "run_type": "aggregate"})
        self.assertEqual(REPORT.summarize(self.document, self.metadata)[0]["median_ns_per_key"], 10)

    def test_writes_reports_with_contract_and_framework_warnings(self):
        self.metadata.update({
            "min_time": 0.1, "known_duplicate_defect_reproduced": True,
            "measurement_warnings": ["Failed to set thread affinity"],
        })
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "benchmarks.json").write_text(json.dumps(self.document))
            (directory / "metadata.json").write_text(json.dumps(self.metadata))
            self.assertEqual(REPORT.write_reports(directory), 3)
            text = (directory / "summary.md").read_text()
            self.assertIn("keep, improve, or replace a collection", text)
            self.assertIn("Known functional defect", text)
            self.assertIn("Failed to set thread affinity", text)
            self.assertFalse(any(line.startswith("|") for line in text.splitlines()))
            self.assertEqual(len(json.loads((directory / "summary.json").read_text())), 3)
            self.assertEqual(len((directory / "summary.csv").read_text().splitlines()), 4)
            self.assertIn("](summary.html)", text)
            self.assertLess(text.index("graphs/collection-1.svg"), text.index("## Data exports"))
            graph = ET.parse(directory / "graphs" / "collection-1.svg")
            points = graph.findall(".//{http://www.w3.org/2000/svg}circle")
            labels = graph.findall(".//{http://www.w3.org/2000/svg}text")
            self.assertEqual(sum(label.text == "Lower is better" for label in labels), 1)
            self.assertEqual(
                [(point.attrib["data-implementation"], float(point.attrib["data-value"]))
                 for point in points], [("runtime", 10), ("std", 20), ("abseil", 5)])
            self.assertEqual({point.attrib["data-metric"] for point in points}, {"median_ns_per_key"})
            html = (directory / "summary.html").read_text()
            self.assertIn("<svg ", html)
            self.assertIn("Known functional defect", html)
            self.assertIn("Failed to set thread affinity", html)
            tooltips = graph.findall(".//{http://www.w3.org/2000/svg}rect[@data-tooltip]")
            self.assertEqual(len(tooltips), 1)
            tooltip = "".join(tooltips[0].itertext())
            self.assertIn("std: 20.0; 0.500x vs runtime; CV 0.00%", tooltip)
            self.assertIn("abseil: 5.0; 2.000x vs runtime", tooltip)

    def test_graph_tooltips_keep_ties_variability_and_all_sizes(self):
        rows = REPORT.summarize(self.document, self.metadata)
        for row, time, cv in zip(list(rows), (10, 10, 20), (5.01, 5, 0)):
            rows.append({
                **row, "size": 64,
                "median_ns_per_key": time, "speedup_vs_baseline": 10 / time,
                "cv_percent": cv,
            })
        for row in rows[:3]:
            rows.append({**row, "scenario": "iterate"})
        text = REPORT.markdown(list(reversed(rows)), self.document["context"], self.metadata)
        self.assertIn("Noise warning", text)
        root = ET.fromstring(REPORT.collection_graph(
            "", list(reversed(rows)), IMPLEMENTATIONS,
            {name: ("#0072b2", "none") for name in IMPLEMENTATIONS}))
        tooltips = ["".join(element.itertext()) for element in root.findall(
            ".//{http://www.w3.org/2000/svg}rect[@data-tooltip]")]
        self.assertEqual(len(tooltips), 3)
        self.assertTrue(tooltips[0].startswith("iterate: 8 elements"))
        self.assertTrue(tooltips[1].startswith("lookup_hit: 8 elements"))
        self.assertTrue(tooltips[2].startswith("lookup_hit: 64 elements"))
        self.assertIn("runtime: 10; 1.000x vs runtime; CV 5.01%", tooltips[2])
        self.assertIn("std: 10; 1.000x vs runtime; CV 5.00%", tooltips[2])

    def test_html_index_has_one_link_per_collection_regardless_of_scenarios_or_metrics(self):
        class NavigationParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.links = []
                self.rows = 0

            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                if "id" in attributes:
                    self.ids.append(attributes["id"])
                if tag == "a" and attributes.get("href", "").startswith("#"):
                    self.links.append(attributes["href"][1:])
                if tag == "tr":
                    self.rows += 1

        self.expand_collections()
        rows = REPORT.summarize(self.document, self.metadata)
        rows += [{**row, "scenario": "future<&test"} for row in rows]
        memory = REPORT.validate_memory(
            self.memory_document(), self.document["context"], self.metadata["expected_benchmarks"])
        memory["cases"] += [{**row, "scenario": "future<&test"} for row in memory["cases"]]
        styles = {name: ("#0072b2", "none") for name in IMPLEMENTATIONS}
        for measurements in (None, memory):
            graphs = [
                (collection, REPORT.collection_graph(
                    collection, [row for row in rows if row["collection"] == collection],
                    IMPLEMENTATIONS, styles,
                    [row for row in measurements["cases"] if row["collection"] == collection]
                    if measurements is not None else None))
                for collection in COLLECTIONS
            ]
            html = REPORT.html_summary(
                rows, self.document["context"], self.metadata, measurements, graphs)
            parser = NavigationParser()
            parser.feed(html)
            expected = {f"collection-{index}" for index in range(1, len(COLLECTIONS) + 1)}
            self.assertEqual(set(parser.links), expected)
            self.assertEqual(len(parser.links), len(expected))
            self.assertTrue(expected.issubset(parser.ids))
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            self.assertEqual(parser.rows, 1 + len(COLLECTIONS))
            self.assertLess(html.index("<nav "), html.index("<svg "))
            self.assertIn("future&lt;&amp;test", html)
            navigation = html.split("<nav ", 1)[1].split("</nav>", 1)[0]
            self.assertNotIn("lookup_hit", navigation)
            self.assertNotIn("future", navigation)
            self.assertIn("runtime, std, abseil", navigation)

    def test_collection_only_report_does_not_claim_functional_validation(self):
        self.metadata.update({
            "functional_tests": "not_run",
            "known_duplicate_defect_reproduced": None,
        })
        rows = REPORT.summarize(self.document, self.metadata)
        text = REPORT.markdown(rows, self.document["context"], self.metadata)
        self.assertIn("Functional validation was not run", text)
        self.assertIn("illustrative artifact warning", text)
        self.assertNotIn("Linux", text)
        self.assertNotIn("duplicate keys", text)

    def test_summary_omits_repository_inventory_but_preserves_metadata(self):
        self.metadata.update({"project_status": " M checkout-file.cpp", "project_commit": "abc"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "benchmarks.json").write_text(json.dumps(self.document))
            original = json.dumps(self.metadata)
            (directory / "metadata.json").write_text(original)
            REPORT.write_reports(directory)
            text = (directory / "summary.md").read_text()
            self.assertNotIn("checkout-file.cpp", text)
            self.assertNotIn("project_commit", text)
            self.assertNotIn("Run setting", text)
            self.assertEqual((directory / "metadata.json").read_text(), original)
            html = (directory / "summary.html").read_text()
            self.assertNotIn("checkout-file.cpp", html)
            self.assertNotIn("project_commit", html)

    def test_graphs_preserve_collections_tests_sizes_and_escape_labels(self):
        self.expand_collections()
        rows = REPORT.summarize(self.document, self.metadata)
        original = list(rows)
        rows += [{**row, "size": 64, "median_ns_per_key": row["median_ns_per_key"] * 2}
                 for row in original]
        rows += [{**row, "scenario": "future<&test", "size": 128} for row in original]
        memory = REPORT.validate_memory(
            self.memory_document(), self.document["context"], self.metadata["expected_benchmarks"])
        styles = {name: ("#0072b2", "none") for name in IMPLEMENTATIONS}
        for multiplier, collection in enumerate(COLLECTIONS, 1):
            selected = [row for row in rows if row["collection"] == collection]
            cases = [row for row in memory["cases"] if row["collection"] == collection]
            original_cases = list(cases)
            cases += [{**row, "size": 64} for row in original_cases]
            cases += [{**row, "scenario": "future<&test", "size": 128,
                       "footprint_bytes_per_key": 0} for row in original_cases]
            root = ET.fromstring(REPORT.collection_graph(
                collection + "<&", selected, IMPLEMENTATIONS, styles, cases))
            points = root.findall(".//{http://www.w3.org/2000/svg}circle")
            self.assertEqual(len(points), 18)
            cpu = [point for point in points if point.attrib["data-metric"] == "median_ns_per_key"]
            actual = {(point.attrib["data-implementation"], int(point.attrib["data-size"])):
                      float(point.attrib["data-value"]) for point in cpu}
            self.assertEqual(actual[("runtime", 8)], 10 * multiplier)
            self.assertEqual(actual[("runtime", 64)], 20 * multiplier)
            positions = {int(point.attrib["data-size"]): point.attrib for point in cpu
                         if point.attrib["data-implementation"] == "runtime"}
            self.assertLess(float(positions[8]["cx"]), float(positions[64]["cx"]))
            self.assertLess(float(positions[64]["cy"]), float(positions[8]["cy"]))
            labels = "".join(root.itertext())
            self.assertIn("future<&test", labels)
            self.assertIn(collection + "<&", labels)
            panel_labels = root.findall(".//{http://www.w3.org/2000/svg}text")
            self.assertEqual(sum(label.text == "Lower is better" for label in panel_labels), 4)

    def expand_collections(self):
        samples = self.document["benchmarks"]
        expected = self.metadata["expected_benchmarks"]
        self.document["context"]["collections"] = ",".join(COLLECTIONS)
        self.document["context"].update({
            "collection." + name: "Artifact description for " + name for name in COLLECTIONS
        })
        self.document["benchmarks"] = [
            {**row, "name": collection + "/" + row["name"],
             "cpu_time": row["cpu_time"] * multiplier}
            for multiplier, collection in enumerate(COLLECTIONS, 1)
            for row in samples
        ]
        self.metadata["expected_benchmarks"] = [
            collection + "/" + name for collection in COLLECTIONS for name in expected
        ]

    def test_groups_key_families_without_mixing_cpu_or_memory(self):
        self.expand_collections()
        rows = REPORT.summarize(self.document, self.metadata)
        self.assertEqual(len(rows), 12)
        for multiplier, collection in enumerate(COLLECTIONS, 1):
            group = [row for row in rows if row["collection"] == collection]
            self.assertEqual([row["median_ns_per_key"] for row in group],
                             [10 * multiplier, 20 * multiplier, 5 * multiplier])
            self.assertEqual([row["speedup_vs_baseline"] for row in group], [1, 0.5, 2])
        document = self.memory_document()
        for row in document["cases"]:
            row["object_bytes"] = 8 * (1 + list(COLLECTIONS).index(
                REPORT.split_name(row["name"])[0]))
        for row in document["lifecycles"]:
            row["object_bytes"] = 8 * (1 + list(COLLECTIONS).index(row["collection"]))
        memory = REPORT.validate_memory(
            document, self.document["context"], self.metadata["expected_benchmarks"])
        self.assertEqual(len(memory["lifecycles"]), 24)
        text = REPORT.markdown(rows, self.document["context"], self.metadata, memory)
        for collection in COLLECTIONS:
            self.assertIn(f"## {collection}\n", text)
            self.assertIn("Artifact description for " + collection, text)
        self.assertEqual(text.count(" comparisons](graphs/"), 4)
        document["context"]["collections"] = "u32"
        with self.assertRaisesRegex(ValueError, "configuration mismatch: collections"):
            REPORT.validate_memory(
                document, self.document["context"], self.metadata["expected_benchmarks"])
        document["context"] = self.document["context"].copy()
        document["lifecycles"].pop()
        with self.assertRaisesRegex(ValueError, "Missing memory lifecycles"):
            REPORT.validate_memory(
                document, self.document["context"], self.metadata["expected_benchmarks"])

    def test_accepts_unknown_workloads_and_rejects_only_malformed_names(self):
        self.assertEqual(REPORT.split_name("future/fourth/compact/128"),
                         ("future", "fourth", "compact", 128))
        for name in ("too/few", "one/two/three/four/8", "family//scenario/8", "a/b/c/0"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                REPORT.split_name(name)

    def test_nonstandard_implementations_baseline_and_lifecycle_modes(self):
        names = {"runtime": "reference", "std": "alternative", "abseil": "candidate"}
        self.metadata["expected_benchmarks"] = [
            "future/" + names[name.split("/")[0]] + "/compact/8"
            for name in self.metadata["expected_benchmarks"]
        ]
        for row in self.document["benchmarks"]:
            row["name"] = "future/" + names[row["name"].split("/")[0]] + "/compact/8"
        self.document["context"]["baseline"] = "candidate"
        rows = REPORT.summarize(self.document, self.metadata)
        self.assertEqual([row["speedup_vs_baseline"] for row in rows], [0.5, 0.25, 1])
        document = self.memory_document()
        document["lifecycle_modes"] = ["retained", "preallocated"]
        for row in document["lifecycles"]:
            row["mode"] = {"growing": "retained", "reserved": "preallocated"}[row["mode"]]
        memory = REPORT.validate_memory(
            document, self.document["context"], self.metadata["expected_benchmarks"])
        text = REPORT.markdown(rows, self.document["context"], self.metadata, memory)
        self.assertIn("Baseline for tooltip speedups: `candidate`.", text)
        self.assertEqual({row["mode"] for row in memory["lifecycles"]},
                         {"retained", "preallocated"})
        self.document["context"]["baseline"] = "absent"
        with self.assertRaisesRegex(ValueError, "Missing comparison baseline"):
            REPORT.summarize(self.document, self.metadata)

    def test_original_memory_artifacts_remain_readable(self):
        document = self.memory_document()
        document["schema_version"] = 1
        document.pop("lifecycle_modes")
        for row in document["cases"]:
            row.pop("includes_destruction")
        for row in document["lifecycles"]:
            row.pop("collection")
        memory = REPORT.validate_memory(
            document, self.document["context"], self.metadata["expected_benchmarks"])
        self.assertEqual(len(memory["cases"]), 3)
        self.assertEqual(len(memory["lifecycles"]), 6)

    def test_per_collection_lifecycle_modes_are_artifact_driven(self):
        self.expand_collections()
        document = self.memory_document()
        document["lifecycle_modes_by_collection"] = {
            COLLECTIONS[0]: ["preallocated"],
            COLLECTIONS[1]: ["growing"],
        }
        lifecycles = []
        for row in document["lifecycles"]:
            if row["collection"] == COLLECTIONS[0]:
                if row["mode"] == "reserved":
                    continue
                row["mode"] = "preallocated"
            elif row["collection"] == COLLECTIONS[1] and row["mode"] == "reserved":
                continue
            lifecycles.append(row)
        document["lifecycles"] = lifecycles
        memory = REPORT.validate_memory(
            document, self.document["context"], self.metadata["expected_benchmarks"])
        text = REPORT.markdown(REPORT.summarize(self.document, self.metadata),
                               self.document["context"], self.metadata, memory)
        self.assertEqual(len(memory["lifecycles"]), 18)
        self.assertIn("memory-lifecycle.csv", text)
        self.assertEqual(sum(row["mode"] == "preallocated" for row in memory["lifecycles"]), 3)
        document["lifecycles"].pop()
        with self.assertRaisesRegex(ValueError, "Missing memory lifecycles"):
            REPORT.validate_memory(
                document, self.document["context"], self.metadata["expected_benchmarks"])

    def test_rejects_malformed_per_collection_lifecycle_modes(self):
        for declaration in ([], {"absent": ["new"]}, {"": []}, {"": [""]},
                            {"": ["new", "new"]}, {"": "new"}):
            document = self.memory_document()
            document["lifecycle_modes_by_collection"] = declaration
            with self.subTest(declaration=declaration), self.assertRaisesRegex(
                    ValueError, "Invalid per-collection lifecycle modes"):
                REPORT.validate_memory(
                    document, self.document["context"], self.metadata["expected_benchmarks"])

    def memory_document(self):
        return {
            "schema_version": 2, "context": copy.deepcopy(self.document["context"]),
            "lifecycle_modes": ["growing", "reserved"],
            "cases": [{
                "name": name, "object_bytes": 8, "before_heap_bytes": 64,
                "includes_destruction": False,
                "after_heap_bytes": 64, "after_destroy_heap_bytes": 0,
                "allocations": 0, "deallocations": 0, "allocated_bytes": 0,
                "freed_bytes": 0, "end_heap_bytes": 64, "peak_heap_bytes": 64,
            } for name in self.metadata["expected_benchmarks"]],
            "lifecycles": [{
                "collection": collection,
                "implementation": name, "mode": mode, "size": 8, "object_bytes": 8,
                "empty_heap_bytes": 0, "populated_heap_bytes": 64,
                "cleared_heap_bytes": 32, "destroyed_heap_bytes": 0,
            } for collection, implementations in REPORT.comparison_inventory(
                self.metadata["expected_benchmarks"]).items()
              for name in implementations for mode in ("growing", "reserved")],
        }

    def test_memory_graphs_and_exports_leave_cpu_numbers_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "benchmarks.json").write_text(json.dumps(self.document))
            (directory / "metadata.json").write_text(json.dumps(self.metadata))
            REPORT.write_reports(directory)
            before = {name: (directory / name).read_bytes()
                      for name in ("summary.json", "summary.csv")}
            (directory / "memory.json").write_text(json.dumps(self.memory_document()))
            cache = "CMAKE_C_FLAGS:STRING=\nCMAKE_CXX_FLAGS:STRING=-O3\n"
            (directory / "CMakeCache.txt").write_text(cache)
            (directory / "memory-CMakeCache.txt").write_text(cache)
            metadata = {
                "dependencies": self.metadata["dependencies"],
                "cpu_artifact_hashes": {
                    name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    for name in ("benchmarks.json", "metadata.json")
                },
            }
            (directory / "memory-metadata.json").write_text(json.dumps(metadata))
            REPORT.write_reports(directory)
            text = (directory / "summary.md").read_text()
            self.assertFalse(any(line.startswith("|") for line in text.splitlines()))
            self.assertIn("No instrumented timings are reported", text)
            memory = json.loads((directory / "memory-summary.json").read_text())
            self.assertEqual(memory["cases"][0]["footprint_bytes"], 72)
            graph = ET.parse(directory / "graphs" / "collection-1.svg")
            points = graph.findall(".//{http://www.w3.org/2000/svg}circle")
            footprint = [float(point.attrib["data-value"]) for point in points
                         if point.attrib["data-metric"] == "footprint_bytes_per_key"]
            self.assertEqual(footprint, [9, 9, 9])
            self.assertEqual((directory / "summary.html").read_text().count("<svg "), 1)
            self.assertEqual(len((directory / "memory-summary.csv").read_text().splitlines()), 4)
            self.assertEqual(before, {name: (directory / name).read_bytes() for name in before})
            metadata["cpu_artifact_hashes"]["benchmarks.json"] = "wrong"
            (directory / "memory-metadata.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "different CPU results"):
                REPORT.write_reports(directory)
            (directory / "memory-CMakeCache.txt").write_text(cache.replace("-O3", "-O0"))
            with self.assertRaisesRegex(ValueError, "build settings mismatch"):
                REPORT.write_reports(directory)

    def test_rejects_invalid_incomplete_or_mismatched_memory(self):
        valid = self.memory_document()
        self.assertEqual(len(REPORT.validate_memory(
            valid, self.document["context"], self.metadata["expected_benchmarks"])["cases"]), 3)
        cases = []
        document = copy.deepcopy(valid)
        document["context"]["compiler"] = "different"
        cases.append(document)
        document = copy.deepcopy(valid)
        document["cases"].pop()
        cases.append(document)
        document = copy.deepcopy(valid)
        document["cases"].append(copy.deepcopy(document["cases"][0]))
        cases.append(document)
        for change in ({"after_destroy_heap_bytes": 1}, {"allocated_bytes": 1},
                       {"peak_heap_bytes": 0}, {"allocations": -1}, {"object_bytes": True}):
            document = copy.deepcopy(valid)
            document["cases"][0].update(change)
            cases.append(document)
        document = copy.deepcopy(valid)
        document["lifecycles"].pop()
        cases.append(document)
        for change in ({"destroyed_heap_bytes": 1}, {"object_bytes": 16}):
            document = copy.deepcopy(valid)
            document["lifecycles"][0].update(change)
            cases.append(document)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ValueError):
                REPORT.validate_memory(
                    document, self.document["context"], self.metadata["expected_benchmarks"])

    def test_build_memory_includes_destruction_but_keeps_populated_checkpoint(self):
        document = self.memory_document()
        for row in document["cases"]:
            row.update({
                "name": row["name"].replace("lookup_hit", "build_growing"),
                "includes_destruction": True,
                "before_heap_bytes": 0, "allocated_bytes": 80, "freed_bytes": 80,
                "end_heap_bytes": 0, "peak_heap_bytes": 80,
                "allocations": 3, "deallocations": 3,
            })
        expected = [row["name"] for row in document["cases"]]
        result = REPORT.validate_memory(document, self.document["context"], expected)["cases"][0]
        self.assertEqual(result["footprint_bytes"], 72)
        self.assertEqual(result["peak_footprint_bytes"], 88)
        document["cases"][0]["deallocations"] = 2
        with self.assertRaisesRegex(ValueError, "lifecycle memory boundary"):
            REPORT.validate_memory(document, self.document["context"], expected)


if __name__ == "__main__":
    unittest.main()
