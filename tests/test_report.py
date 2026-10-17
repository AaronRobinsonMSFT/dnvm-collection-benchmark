import copy
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


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "repetitions": 3,
            "expected_benchmarks": [name + "/lookup_hit/8" for name in REPORT.IMPLEMENTATIONS],
            "dependencies": {"runtime": {"commit": "abc", "dirty": False}},
        }
        self.document = {
            "context": {
                "build_type": "Release", "runtime_simd": "neon",
                "sanitizers": "0", "runtime_revision": "abc",
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
        self.assertEqual([row["speedup_vs_runtime"] for row in rows], [1, 0.5, 2])

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

    def test_rejects_incomplete_comparison(self):
        self.metadata["expected_benchmarks"] = self.metadata["expected_benchmarks"][:-1]
        self.document["benchmarks"] = self.document["benchmarks"][:-3]
        with self.assertRaisesRegex(ValueError, "Missing comparison"):
            REPORT.summarize(self.document, self.metadata)

    def test_rejects_unrepresentative_or_mismatched_build(self):
        for key, value in (("build_type", "Debug"), ("sanitizers", "1"),
                           ("runtime_simd", "scalar"), ("runtime_revision", "wrong")):
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
        self.document["context"].update({
            "compiler": "test", "stdlib": "test", "system": "Darwin",
            "architecture": "arm64", "pointer_bits": "64",
        })
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "benchmarks.json").write_text(json.dumps(self.document))
            (directory / "metadata.json").write_text(json.dumps(self.metadata))
            self.assertEqual(REPORT.write_reports(directory), 3)
            text = (directory / "summary.md").read_text()
            self.assertIn("Known functional defect", text)
            self.assertIn("Failed to set thread affinity", text)
            self.assertIn("ns/key", text)
            self.assertEqual(len(json.loads((directory / "summary.json").read_text())), 3)
            self.assertEqual(len((directory / "summary.csv").read_text().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
