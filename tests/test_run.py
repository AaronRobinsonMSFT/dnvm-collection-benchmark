import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_report


SPEC = importlib.util.spec_from_file_location(
    "run", Path(__file__).resolve().parents[1] / "scripts" / "run.py"
)
RUN = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"report": test_report.REPORT}):
    SPEC.loader.exec_module(RUN)

SPEC = importlib.util.spec_from_file_location(
    "run_memory", Path(__file__).resolve().parents[1] / "scripts" / "run_memory.py"
)
MEMORY = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"report": test_report.REPORT, "run": RUN}):
    SPEC.loader.exec_module(MEMORY)


class RunTests(unittest.TestCase):
    def test_dirty_checkout_preserves_paths_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("runtime", "abseil-cpp", "benchmark"):
                directory = root / "external" / name
                directory.mkdir(parents=True)
                subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
                (directory / "tracked file.txt").write_text("original\n")
                subprocess.run(["git", "-C", str(directory), "add", "."], check=True)
                subprocess.run([
                    "git", "-C", str(directory), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "Initial",
                ], check=True)
            runtime = root / "external" / "runtime"
            (runtime / "tracked file.txt").write_text("changed\n")
            (runtime / "new file.txt").write_text("untracked\n")
            subprocess.run(["git", "-C", str(runtime), "config", "core.longpaths", "false"],
                           check=True)
            snapshot = root / "dependencies.json"
            with mock.patch.object(RUN, "ROOT", root), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "uncommitted changes: runtime"):
                    RUN.check_dependencies(snapshot)
            dependencies = json.loads(snapshot.read_text())
            revision = dependencies["runtime"]
            self.assertTrue(revision["dirty"])
            self.assertTrue(revision["status"].startswith(" M "))
            self.assertIn("tracked file.txt", revision["status"])
            self.assertIn("new file.txt", revision["status"])
            diagnostics = revision["diagnostics"]
            self.assertIn("false", diagnostics["config"]["core.longpaths"])
            self.assertIn("tracked file.txt", diagnostics["diff_raw_with_longpaths"])
            self.assertIn("tracked file.txt", diagnostics["modified_eol_with_longpaths"])
            self.assertIn("new file.txt", diagnostics["status_with_longpaths"])
            self.assertFalse(dependencies["abseil"]["dirty"])
            self.assertFalse(dependencies["benchmark"]["dirty"])

    def test_dirty_dependency_prevents_any_cpu_binary_invocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            suffix = ".exe" if sys.platform == "win32" else ""
            (build / ("map_benchmarks" + suffix)).touch()
            output = root / "results"
            dependencies = {"runtime": {"commit": "abc", "dirty": True, "status": " M changed.c"}}
            with mock.patch.object(sys, "argv", [
                    "run.py", "--build", str(build), "--output", str(output)]), \
                    mock.patch.object(RUN, "dependency_inventory", return_value=dependencies), \
                    mock.patch.object(RUN.subprocess, "check_output") as command, \
                    mock.patch.object(RUN.subprocess, "run") as process:
                with self.assertRaisesRegex(ValueError, "changed.c"):
                    RUN.main()
            command.assert_not_called()
            process.assert_not_called()
            self.assertEqual(json.loads((output / "dependencies.json").read_text()), dependencies)

    def test_longpath_diagnostic_does_not_override_dirty_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "dependencies.json"
            dependencies = {"runtime": {
                "commit": "abc", "dirty": True, "status": " D long/path.c",
                "diagnostics": {"status_with_longpaths": ""},
            }}
            with mock.patch.object(RUN, "dependency_inventory", return_value=dependencies):
                with self.assertRaisesRegex(ValueError, "long/path.c"):
                    RUN.check_dependencies(snapshot)
            self.assertEqual(json.loads(snapshot.read_text()), dependencies)

    def test_clean_snapshot_returns_revisions_without_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "dependencies.json"
            dependencies = {"runtime": {"commit": "abc", "dirty": False, "status": ""}}
            with mock.patch.object(RUN, "dependency_inventory", return_value=dependencies):
                self.assertEqual(RUN.check_dependencies(snapshot), dependencies)
                with self.assertRaises(FileExistsError):
                    RUN.check_dependencies(snapshot)
            self.assertEqual(json.loads(snapshot.read_text()), dependencies)

    def test_dirty_dependency_prevents_memory_binary_invocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            suffix = ".exe" if sys.platform == "win32" else ""
            (build / ("map_memory" + suffix)).touch()
            results = root / "results"
            results.mkdir()
            fixture = test_report.ReportTests("test_normalization_and_ratio_direction")
            fixture.setUp()
            for name, document in (("benchmarks.json", fixture.document),
                                   ("metadata.json", fixture.metadata)):
                (results / name).write_text(json.dumps(document))
            dependencies = {"runtime": {"commit": "abc", "dirty": True, "status": " M changed.c"}}
            with mock.patch.object(sys, "argv", [
                    "run_memory.py", "--build", str(build), "--results", str(results)]), \
                    mock.patch.object(MEMORY, "build_settings", return_value={}), \
                    mock.patch.object(RUN, "dependency_inventory", return_value=dependencies), \
                    mock.patch.object(MEMORY.subprocess, "run") as process:
                with self.assertRaisesRegex(ValueError, "changed.c"):
                    MEMORY.main()
            process.assert_not_called()
            self.assertEqual(json.loads((results / "memory-dependencies.json").read_text()),
                             dependencies)
