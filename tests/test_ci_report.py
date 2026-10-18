import hashlib
from html.parser import HTMLParser
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

PLATFORMS = {
    "linux-gcc-x64": ("Linux", "GNU", "64", "x86_64", "sse2"),
    "linux-clang-x64": ("Linux", "Clang", "64", "x86_64", "sse2"),
    "macos-arm64": ("Darwin", "AppleClang", "64", "arm64", "neon"),
    "windows-x64": ("Windows", "MSVC", "64", "x64", "sse2"),
    "windows-x86": ("Windows", "MSVC", "32", "x86", "sse2"),
}
VALIDATION_RUNS = ("linux-gcc-x64", "linux-clang-x64")


class SummaryLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.svg_count = 0
        self.navigation_rows = 0
        self.in_navigation = False

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "a":
            self.links.append(attributes["href"])
        if tag == "svg":
            self.svg_count += 1
        if tag == "nav":
            self.in_navigation = True
        if tag == "tr" and self.in_navigation:
            self.navigation_rows += 1

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_navigation = False


class CombinedReportTests(unittest.TestCase):
    commit = "f" * 40

    def populate(self, directory, multiple_collections=False, platforms=None):
        platforms = PLATFORMS if platforms is None else platforms
        for name, (system, compiler, bits, architecture, simd) in platforms.items():
            (directory / ("run-" + name + ".json")).write_text(json.dumps({
                "schema_version": 1, "name": name, "commit": self.commit,
                "expected_runs": len(platforms), "measurements": "results-" + name,
                "validation": "validation-" + name if name in VALIDATION_RUNS else None,
            }))
            fixture = test_report.ReportTests("test_normalization_and_ratio_direction")
            fixture.setUp()
            if multiple_collections:
                fixture.expand_collections()
            fixture.document["context"].update({
                "system": system, "compiler": compiler + " 1.0",
                "pointer_bits": bits, "architecture": architecture, "runtime_simd": simd,
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
        for name in VALIDATION_RUNS:
            if name not in platforms:
                continue
            validation = directory / ("validation-" + name)
            validation.mkdir()
            (validation / "commit.txt").write_text(self.commit)
            (validation / "functional-tests.xml").write_text(
                '<testsuite tests="3"><testcase name="contract-a"/>'
                '<testcase name="contract-b"/>'
                '<testcase name="contract-c"/></testsuite>'
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
            for name in PLATFORMS:
                self.assertIn(f"<summary>{name}</summary>", text)
                image = f"results-{name}/graphs/collection-1.svg"
                self.assertIn("](" + image + ")", text)
                self.assertTrue((directory / image).is_file())
                self.assertIn(f"](results-{name}/summary.html)", text)
                self.assertTrue((directory / f"results-{name}/summary.html").is_file())

    def test_measurement_only_inventory_does_not_require_functional_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            for path in directory.glob("run-*.json"):
                descriptor = json.loads(path.read_text())
                descriptor.pop("validation")
                path.write_text(json.dumps(descriptor))
            for path in directory.glob("validation-*/functional-tests.xml"):
                path.unlink()
            result = CI.combine(directory, self.commit)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["platforms"]), 5)
            self.assertEqual(result["functional"], {})
            self.assertNotIn("## Functional validation", (directory / "summary.md").read_text())
            self.assertEqual(len((directory / "summary.csv").read_text().splitlines()), 16)
            self.assertEqual(len((directory / "memory-summary.csv").read_text().splitlines()), 16)

    def test_html_keeps_all_graphs_hover_values_and_collection_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory, multiple_collections=True)
            CI.combine(directory, self.commit)
            text = (directory / "summary.html").read_text()
            parser = SummaryLinks()
            parser.feed(text)
            self.assertEqual(parser.svg_count, len(PLATFORMS) * len(test_report.COLLECTIONS))
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            self.assertEqual(parser.navigation_rows, 1 + len(test_report.COLLECTIONS))
            self.assertLess(text.index("<nav "), text.index("<svg "))
            for link in parser.links:
                if link.startswith("#"):
                    self.assertIn(link[1:], parser.ids)
                else:
                    self.assertTrue((directory / link).is_file(), link)
            tooltips = 0
            for name in PLATFORMS:
                platform = directory / ("results-" + name)
                for path in (platform / "graphs").glob("*.svg"):
                    self.assertIn(path.read_text(), text)
                tooltips += (platform / "summary.html").read_text().count('data-tooltip="true"')
            self.assertEqual(text.count('data-tooltip="true"'), tooltips)
            navigation = text.split("<nav ", 1)[1].split("</nav>", 1)[0]
            self.assertNotIn("lookup_hit", navigation)
            self.assertIn("Lower is better", text)
            self.assertIn("not across machines", text)

    def test_html_includes_escaped_run_specific_findings_and_rejects_stale_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            notes = {
                "commit": self.commit,
                "methodology": "Within-run comparisons <only>",
                "findings": [{"finding": "Finding <one>", "detail": "Input data, not a <script>."}],
                "action_items": [{"action": "Follow up", "detail": "Investigate A & B."}],
            }
            path = directory / "executive-summary.json"
            path.write_text(json.dumps(notes))
            self.assertEqual(CI.combine(directory, self.commit)["errors"], [])
            text = (directory / "summary.html").read_text()
            self.assertIn("Finding &lt;one&gt;", text)
            self.assertIn("not a &lt;script&gt;", text)
            self.assertIn("Investigate A &amp; B.", text)
            self.assertIn("Within-run comparisons &lt;only&gt;", text)
            for changes in (
                    {"commit": "wrong"},
                    {"methodology": 42},
                    {"findings": [{"finding": "Missing detail"}]},
                    {"action_items": ["Invalid entry"]}):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps({**notes, **changes}))
                    result = CI.combine(directory, self.commit)
                    self.assertEqual(len(result["errors"]), 1)
                    self.assertIn("executive-summary.json", result["errors"][0])
                    text = (directory / "summary.html").read_text()
                    self.assertIn("<h2>Errors</h2>", text)
                    self.assertNotIn("Finding &lt;one&gt;", text)

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
                text = (directory / "summary.html").read_text()
                self.assertIn("Missing or invalid", text)
                self.assertIn("<h2>Errors</h2>", text)
                parser = SummaryLinks()
                parser.feed(text)
                self.assertEqual(parser.svg_count, 4)
                for link in parser.links:
                    if link.startswith("#"):
                        self.assertIn(link[1:], parser.ids)

    def test_combines_all_key_families_and_keeps_their_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory, multiple_collections=True)
            result = CI.combine(directory, self.commit)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["platforms"]), 5)
            for platform in result["platforms"]:
                self.assertEqual(len(platform["cpu"]), 12)
                self.assertEqual(len(platform["memory"]["cases"]), 12)
                self.assertEqual(len(platform["memory"]["lifecycles"]), 24)
                self.assertEqual({row["collection"] for row in platform["cpu"]},
                                 set(test_report.COLLECTIONS))
            self.assertEqual(len((directory / "summary.csv").read_text().splitlines()), 61)
            self.assertEqual(len((directory / "memory-summary.csv").read_text().splitlines()), 61)
            self.assertEqual(len((directory / "memory-lifecycle.csv").read_text().splitlines()), 121)

    def test_functional_failure_or_missing_group_cannot_report_success(self):
        for content in (
            '<testsuite tests="3"><testcase name="map_contract"><failure/></testcase>'
            '<testcase name="memory_accounting"/><testcase name="report_contract"/></testsuite>',
            '<testsuite tests="3"><testcase name="map_contract"><skipped/></testcase>'
            '<testcase name="memory_accounting"/><testcase name="report_contract"/></testsuite>',
            '<testsuite tests="3"><testcase name="map_contract"/></testsuite>',
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
            for name in PLATFORMS:
                (directory / ("results-" + name) / "memory.json").unlink()
            result = CI.combine(directory, self.commit)
            self.assertEqual(len(result["errors"]), 5)
            self.assertEqual(result["platforms"], [])
            for name in ("summary.csv", "memory-summary.csv", "memory-lifecycle.csv"):
                self.assertFalse((directory / name).exists())
            text = (directory / "summary.html").read_text()
            self.assertNotIn("<svg ", text)
            self.assertNotIn('href="summary.csv"', text)
            self.assertIn("Missing or invalid", text)

    def test_discovers_unfamiliar_platforms_and_changed_test_inventories(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory, platforms={
                **PLATFORMS, "future-platform": ("FutureOS", "FutureCompiler", "128", "future", "future-simd"),
            })
            (directory / "validation-linux-gcc-x64" / "functional-tests.xml").write_text(
                '<testsuite tests="1"><testcase name="completely-new-test"/></testsuite>'
            )
            result = CI.combine(directory, self.commit)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["platforms"]), 6)
            self.assertEqual(result["functional"]["linux-gcc-x64"], "passed")
            self.assertIn("future-platform | complete | 3 | 3", (directory / "summary.md").read_text())
            parser = SummaryLinks()
            parser.feed((directory / "summary.html").read_text())
            self.assertEqual(parser.svg_count, 6)
            self.assertIn("results-future-platform/summary.html", parser.links)

    def test_missing_run_descriptor_or_entire_artifact_cannot_report_success(self):
        for filename in ("run-windows-x86.json", "results-windows-x86/benchmarks.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                (directory / filename).unlink()
                result = CI.combine(directory, self.commit)
                self.assertEqual(len(result["errors"]), 1)
                self.assertEqual(len(result["platforms"]), 4)

    def test_validation_uses_junit_suite_counts_and_qualified_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            path = directory / "validation-linux-gcc-x64" / "functional-tests.xml"
            path.write_text(
                '<testsuites><testsuite name="first" tests="1"><testcase name="same"/></testsuite>'
                '<testsuite name="second" tests="1"><testcase name="same"/></testsuite></testsuites>'
            )
            self.assertEqual(CI.combine(directory, self.commit)["errors"], [])
            path.write_text(
                '<testsuites><testsuite name="first" tests="2"><testcase name="one"/></testsuite>'
                '<testsuite name="second" tests="0"><testcase name="two"/></testsuite></testsuites>'
            )
            self.assertEqual(CI.combine(directory, self.commit)["functional"]["linux-gcc-x64"], "failed")

    def test_invalid_inventory_cannot_reference_other_directories_or_commits(self):
        for changes in ({"measurements": "../outside"}, {"measurements": "/outside"},
                        {"commit": "wrong"}, {"name": "linux-gcc-x64"},
                        {"measurements": "results-linux-gcc-x64"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                path = directory / "run-windows-x86.json"
                descriptor = json.loads(path.read_text())
                descriptor.update(changes)
                path.write_text(json.dumps(descriptor))
                result = CI.combine(directory, self.commit)
                self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
