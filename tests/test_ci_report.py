import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import test_report


SPEC = importlib.util.spec_from_file_location(
    "ci_report", Path(__file__).resolve().parents[1] / "scripts" / "ci_report.py"
)
CI = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"report": test_report.REPORT}):
    SPEC.loader.exec_module(CI)


class CombinedReportTests(unittest.TestCase):
    commit = "f" * 40

    def populate(self, directory):
        for name, (system, compiler, bits, architectures, simd) in CI.PLATFORMS.items():
            fixture = test_report.ReportTests("test_normalization_and_ratio_direction")
            fixture.setUp()
            fixture.document["context"].update({
                "system": system, "compiler": compiler + " 1.0",
                "pointer_bits": bits, "architecture": architectures[0], "runtime_simd": simd,
            })
            fixture.metadata.update({
                "project_commit": self.commit, "project_status": "",
                "functional_tests": "not_run", "known_duplicate_defect_reproduced": None,
                "dependencies": {
                    key: {"commit": revision, "dirty": False}
                    for key, revision in (("runtime", "abc"), ("abseil", "def"), ("benchmark", "ghi"))
                },
            })
            platform = directory / ("results-" + name)
            platform.mkdir()
            for filename, document in (
                ("benchmarks.json", fixture.document),
                ("metadata.json", fixture.metadata),
                ("memory.json", fixture.memory_document()),
            ):
                (platform / filename).write_text(json.dumps(document))
            memory_metadata = {
                "project_commit": self.commit, "project_status": "",
                "functional_tests": "not_run", "dependencies": fixture.metadata["dependencies"],
                "cpu_artifact_hashes": {
                    filename: hashlib.sha256((platform / filename).read_bytes()).hexdigest()
                    for filename in ("benchmarks.json", "metadata.json")
                },
            }
            (platform / "memory-metadata.json").write_text(json.dumps(memory_metadata))
            cache = "CMAKE_C_FLAGS:STRING=\nCMAKE_CXX_FLAGS:STRING=-O3\n"
            for filename in ("CMakeCache.txt", "memory-CMakeCache.txt"):
                (platform / filename).write_text(cache)
        for name in CI.VALIDATION_PLATFORMS:
            validation = directory / ("validation-" + name)
            validation.mkdir()
            (validation / "commit.txt").write_text(self.commit)
            (validation / "functional-tests.xml").write_text(
                '<testsuite><testcase name="map_contract"/>'
                '<testcase name="memory_accounting"/>'
                '<testcase name="report_contract"/></testsuite>'
            )

    def test_combines_all_platforms_without_pooling_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            result = CI.combine(directory, self.commit)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["platforms"]), 5)
            self.assertEqual(set(result["functional"].values()), {"passed"})
            for name in ("summary.csv", "memory-summary.csv"):
                self.assertEqual(len((directory / name).read_text().splitlines()), 16)
            self.assertEqual(len((directory / "memory-lifecycle.csv").read_text().splitlines()), 31)
            text = (directory / "summary.md").read_text()
            self.assertIn("windows-x86 | complete | 3 | 3", text)
            self.assertIn("not across machines", text)
            for name in CI.PLATFORMS:
                self.assertIn(f"<summary>{name}</summary>", text)

    def test_reports_partial_results_and_platform_or_commit_mismatches(self):
        cases = ("missing", "commit", "architecture", "dataset")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                platform = directory / "results-windows-x86"
                if case == "missing":
                    (platform / "memory.json").unlink()
                else:
                    filename = "metadata.json" if case == "commit" else "benchmarks.json"
                    path = platform / filename
                    document = json.loads(path.read_text())
                    if case == "commit":
                        document["project_commit"] = "wrong"
                    elif case == "architecture":
                        document["context"]["pointer_bits"] = "64"
                    else:
                        document["context"]["dataset_seed"] = "456"
                    path.write_text(json.dumps(document))
                    if case == "dataset":
                        path = platform / "memory.json"
                        memory = json.loads(path.read_text())
                        memory["context"]["dataset_seed"] = "456"
                        path.write_text(json.dumps(memory))
                        path = platform / "memory-metadata.json"
                        metadata = json.loads(path.read_text())
                        metadata["cpu_artifact_hashes"]["benchmarks.json"] = hashlib.sha256(
                            (platform / "benchmarks.json").read_bytes()
                        ).hexdigest()
                        path.write_text(json.dumps(metadata))
                result = CI.combine(directory, self.commit)
                self.assertEqual(len(result["platforms"]), 4)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIn("windows-x86", result["errors"][0])
                self.assertIn("windows-x86 | missing or invalid", (directory / "summary.md").read_text())

    def test_functional_failure_or_missing_group_cannot_report_success(self):
        for content in (
            '<testsuite><testcase name="map_contract"><failure/></testcase>'
            '<testcase name="memory_accounting"/><testcase name="report_contract"/></testsuite>',
            '<testsuite><testcase name="map_contract"><skipped/></testcase>'
            '<testcase name="memory_accounting"/><testcase name="report_contract"/></testsuite>',
            '<testsuite><testcase name="map_contract"/></testsuite>',
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                (directory / "validation-linux-gcc-x64" / "functional-tests.xml").write_text(content)
                result = CI.combine(directory, self.commit, "failure")
                self.assertEqual(result["functional"]["linux-gcc-x64"], "failed")
                self.assertEqual(result["functional"]["linux-clang-x64"], "passed")
                self.assertEqual(len(result["errors"]), 2)
                self.assertIn("## Errors", (directory / "summary.md").read_text())

    def test_rerun_cannot_leave_stale_exports_when_no_platform_results_are_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            CI.combine(directory, self.commit)
            for name in CI.PLATFORMS:
                (directory / ("results-" + name) / "memory.json").unlink()
            result = CI.combine(directory, self.commit)
            self.assertEqual(len(result["errors"]), 5)
            self.assertEqual(result["platforms"], [])
            for name in ("summary.csv", "memory-summary.csv", "memory-lifecycle.csv"):
                self.assertFalse((directory / name).exists())


if __name__ == "__main__":
    unittest.main()
