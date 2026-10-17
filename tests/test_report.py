import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
            self.assertIn(
                "> On this configuration, for this runtime-derived workload, "
                "how do the alternatives compare with runtime?", text
            )
            self.assertIn("not an answer to that interpretive question", text)
            self.assertIn("keep, improve, or replace the runtime collection", text)
            self.assertIn(
                "the reader's judgment, informed by the collection contract "
                "and application requirements.", text
            )
            self.assertIn("Known functional defect", text)
            self.assertIn("Failed to set thread affinity", text)
            self.assertIn("## lookup_hit\n", text)
            self.assertIn("| 8 | 10.000 | 20.000 (0.50x) | **5.000** (2.00x) |", text)
            self.assertEqual(len(json.loads((directory / "summary.json").read_text())), 3)
            self.assertEqual(len((directory / "summary.csv").read_text().splitlines()), 4)

    def test_markdown_groups_sizes_and_highlights_fastest_ties_and_noise(self):
        rows = REPORT.summarize(self.document, self.metadata)
        for implementation, time, cv in (("runtime", 10, 5.01), ("std", 10, 5),
                                         ("abseil", 20, 0)):
            rows.append({
                "scenario": "lookup_hit", "size": 64, "implementation": implementation,
                "median_ns_per_key": time, "speedup_vs_baseline": 10 / time,
                "cv_percent": cv,
            })
        for row in rows[:3]:
            rows.append({**row, "scenario": "iterate"})
        text = REPORT.markdown(list(reversed(rows)), self.document["context"], self.metadata)
        self.assertEqual(text.count("| Keys | runtime ns/key |"), 2)
        self.assertLess(text.index("## iterate\n"), text.index("## lookup_hit\n"))
        lookup = text.split("## lookup_hit\n")[1].split("<details>")[0]
        small = "| 8 | 10.000 | 20.000 (0.50x) | **5.000** (2.00x) |"
        large = "| 64 | **10.000** [!] | **10.000** (1.00x) | 20.000 (0.50x) |"
        self.assertLess(lookup.index(small), lookup.index(large))
        self.assertIn("<summary>Variability details (CV %)</summary>", text)
        self.assertIn("| lookup_hit | 64 | 5.01 | 5.00 | 0.00 |", text)

    def test_collection_only_report_does_not_claim_functional_validation(self):
        self.metadata.update({
            "functional_tests": "not_run",
            "known_duplicate_defect_reproduced": None,
        })
        rows = REPORT.summarize(self.document, self.metadata)
        text = REPORT.markdown(rows, self.document["context"], self.metadata)
        self.assertIn("| functional_tests | not_run |", text)
        self.assertIn("illustrative artifact warning", text)
        self.assertNotIn("Linux", text)
        self.assertNotIn("duplicate keys", text)

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
        self.assertEqual(text.count("### lookup_hit\n"), 4)
        self.assertEqual(text.count("#### Memory accounting\n"), 4)
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
        self.assertIn("Baseline: `candidate`.", text)
        self.assertIn("reference ns/key (speedup)", text)
        self.assertIn("### retained", text)
        self.assertIn("### preallocated", text)
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

    def test_memory_footprint_is_adjacent_and_cpu_numbers_are_unchanged(self):
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
            self.assertLess(text.index("### Performance"), text.index("### Memory accounting"))
            self.assertLess(text.index("### Memory accounting"), text.index("## Lifecycle"))
            self.assertIn("| 8 | **9.000** | **9.000** | **9.000** |", text)
            self.assertIn("no instrumented timings are reported", text)
            memory = json.loads((directory / "memory-summary.json").read_text())
            self.assertEqual(memory["cases"][0]["footprint_bytes"], 72)
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
