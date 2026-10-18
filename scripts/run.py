"""Collect repeated CPU and memory measurements and write the local report."""

import argparse
import datetime
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from report import write_reports


ROOT = Path(__file__).resolve().parents[1]


def git(*arguments, directory=ROOT):
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True
    ).rstrip("\r\n")


def dependency_inventory():
    dependencies = {}
    for name, path in (("runtime", "runtime"), ("abseil", "abseil-cpp"), ("benchmark", "benchmark")):
        directory = ROOT / "external" / path
        status = git("status", "--porcelain=v1", "--untracked-files=all", directory=directory)
        revision = {
            "commit": git("rev-parse", "HEAD", directory=directory),
            "dirty": bool(status),
            "status": status,
        }
        if status:
            print(f"Dirty dependency {name}:\n{status}", file=sys.stderr, flush=True)
            revision["diagnostics"] = {
                "directory": str(directory),
                "git_version": git("--version", directory=directory),
                "config": {
                    key: git("config", "--show-origin", "--get", "--default", "<unset>", key,
                             directory=directory)
                    for key in ("core.longpaths", "core.autocrlf", "core.symlinks",
                                "core.ignorecase", "core.filemode")
                },
                "status_with_longpaths": git(
                    "-c", "core.longpaths=true", "status", "--porcelain=v1",
                    "--untracked-files=all", directory=directory),
                "diff_raw_with_longpaths": git(
                    "-c", "core.longpaths=true", "diff", "--no-ext-diff", "--no-color",
                    "--raw", "HEAD", "--", directory=directory),
                "modified_eol_with_longpaths": git(
                    "-c", "core.longpaths=true", "ls-files", "--eol", "--modified",
                    directory=directory),
            }
        dependencies[name] = revision
    return dependencies


def check_dependencies(destination):
    dependencies = dependency_inventory()
    with destination.open("x") as snapshot:
        snapshot.write(json.dumps(dependencies, indent=2) + "\n")
    for name, revision in dependencies.items():
        if revision["dirty"]:
            raise ValueError(f"Dependency has uncommitted changes: {name}\n"
                             f"{revision['status']}\nDiagnostics: {destination}")
    return dependencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("build/release"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--min-time", type=float, default=0.1)
    args = parser.parse_args()
    if args.repetitions < 3 or not math.isfinite(args.min_time) or args.min_time <= 0:
        parser.error("Use at least three repetitions and a positive finite minimum time")
    build = args.build.resolve()
    output = args.output.resolve()
    executable_suffix = ".exe" if sys.platform == "win32" else ""
    binary_directory = build / "Release" if (build / "Release").is_dir() else build
    benchmark = binary_directory / ("map_benchmarks" + executable_suffix)
    if not benchmark.is_file():
        parser.error("Build map_benchmarks first; see readme.md")
    if output.exists():
        parser.error("Output already exists; choose a new directory to preserve previous results")
    output.mkdir(parents=True)
    dependencies = check_dependencies(output / "dependencies.json")
    expected = subprocess.check_output(
        [str(benchmark), "--benchmark_list_tests=true", "--benchmark_filter=."], text=True
    ).splitlines()
    metadata = {
        "schema_version": 1,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip() if sys.platform == "darwin" else platform.processor(),
        "logical_cpus": os.cpu_count(),
        "project_commit": git("rev-parse", "--verify", "HEAD") if git("rev-list", "--all", "--max-count=1") else None,
        "project_status": git("status", "--porcelain"),
        "dependencies": dependencies,
        "repetitions": args.repetitions,
        "min_time": args.min_time,
        "expected_benchmarks": expected,
        "functional_tests": "not_run",
    }
    command = [
        str(benchmark),
        "--benchmark_filter=.",
        f"--benchmark_repetitions={args.repetitions}",
        f"--benchmark_min_time={args.min_time}s",
        "--benchmark_enable_random_interleaving=true",
        "--benchmark_report_aggregates_only=false",
        "--benchmark_display_aggregates_only=true",
        f"--benchmark_out={output / 'benchmarks.json'}",
        "--benchmark_out_format=json",
    ]
    metadata["command"] = command
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copyfile(build / "CMakeCache.txt", output / "CMakeCache.txt")
    commands = build / "compile_commands.json"
    if commands.exists():
        shutil.copyfile(commands, output / "compile_commands.json")
    with (output / "benchmark.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    metadata["measurement_warnings"] = [
        line for line in (output / "benchmark.log").read_text().splitlines()
        if "WARNING" in line or "Unable to determine clock rate" in line
    ]
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows = write_reports(output)
    print(f"Completed {rows} CPU comparison rows. Report: {output / 'summary.md'}", flush=True)
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "run_memory.py"),
        "--build", str(build), "--results", str(output),
    ], check=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError, KeyError) as error:
        sys.exit(f"Run failed: {error}")
