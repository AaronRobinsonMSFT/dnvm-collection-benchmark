"""Attach a separate, untimed memory-accounting run to existing CPU results."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from report import build_settings, summarize, validate_memory, write_reports
from run import ROOT, check_dependencies, git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("build/release"))
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    results = args.results.resolve()
    cpu = json.loads((results / "benchmarks.json").read_text())
    cpu_metadata = json.loads((results / "metadata.json").read_text())
    summarize(cpu, cpu_metadata)
    if any((results / name).exists() for name in (
            "memory.json", "memory-metadata.json", "memory.log", "memory-dependencies.json")):
        parser.error("Memory artifacts already exist; choose a new result directory")
    suffix = ".exe" if sys.platform == "win32" else ""
    binary_directory = build / "Release" if (build / "Release").is_dir() else build
    binary = binary_directory / ("map_memory" + suffix)
    if not binary.is_file():
        parser.error("Build map_memory first; see readme.md")
    cpu_settings = build_settings(results / "CMakeCache.txt")
    memory_settings = build_settings(build / "CMakeCache.txt")
    if cpu_settings != memory_settings:
        raise ValueError("Memory compiler flags or target settings differ from CPU results")
    dependencies = check_dependencies(results / "memory-dependencies.json")
    for name, revision in dependencies.items():
        if revision["commit"] != cpu_metadata["dependencies"][name]["commit"]:
            raise ValueError("Memory dependency differs from CPU results: " + name)
    metadata = {
        "schema_version": 1,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_commit": git("rev-parse", "HEAD"),
        "project_status": git("status", "--porcelain"),
        "dependencies": dependencies,
        "functional_tests": "not_run",
        "command": [str(binary)],
        "cpu_artifact_hashes": {
            name: hashlib.sha256((results / name).read_bytes()).hexdigest()
            for name in ("benchmarks.json", "metadata.json")
        },
    }
    with (results / "memory.log").open("w") as log:
        completed = subprocess.run([str(binary)], check=True, stdout=subprocess.PIPE,
                                   stderr=log, text=True)
    document = json.loads(completed.stdout)
    validate_memory(document, cpu["context"], cpu_metadata["expected_benchmarks"])
    shutil.copyfile(build / "CMakeCache.txt", results / "memory-CMakeCache.txt")
    if (build / "compile_commands.json").exists():
        shutil.copyfile(build / "compile_commands.json", results / "memory-compile_commands.json")
    (results / "memory.json").write_text(completed.stdout)
    (results / "memory-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    write_reports(results)
    print(f"Attached {len(document['cases'])} memory scenarios and "
          f"{len(document['lifecycles'])} lifecycle probes. Report: {results / 'summary.md'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Memory run failed: {error}")
