"""Run correctness checks, repeated benchmarks, and the local report."""

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
    ).strip()


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
    tests = binary_directory / ("map_tests" + executable_suffix)
    if not benchmark.is_file() or not tests.is_file():
        parser.error("Build map_benchmarks and map_tests first; see readme.md")
    if output.exists():
        parser.error("Output already exists; choose a new directory to preserve previous results")
    output.mkdir(parents=True)
    cmake_bin = shutil.which("cmake")
    if cmake_bin is None:
        parser.error("System cmake is required on PATH; see readme.md")
    ctest = str(Path(cmake_bin).with_name("ctest" + executable_suffix))
    with (output / "tests.log").open("w") as log:
        subprocess.run([ctest, "--test-dir", str(build), "-C", "Release",
                        "--output-on-failure", "--no-tests=error"],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    with (output / "duplicate-reproducer.log").open("w") as log:
        duplicate = subprocess.run([str(tests), "--reproduce-duplicate"],
                                   stdout=log, stderr=subprocess.STDOUT)
    if duplicate.returncode not in (0, 1):
        raise RuntimeError("Duplicate reproducer failed unexpectedly; inspect its log")
    expected = subprocess.check_output(
        [str(benchmark), "--benchmark_list_tests=true", "--benchmark_filter=."], text=True
    ).splitlines()
    dependencies = {}
    for name, path in (("runtime", "runtime"), ("abseil", "abseil-cpp"), ("benchmark", "benchmark")):
        directory = ROOT / "external" / path
        dependencies[name] = {
            "commit": git("rev-parse", "HEAD", directory=directory),
            "dirty": bool(git("status", "--porcelain", directory=directory)),
        }
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
        "known_duplicate_defect_reproduced": duplicate.returncode == 1,
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
    print(f"Completed {rows} comparison rows. Report: {output / 'summary.md'}")
    if duplicate.returncode == 1:
        print("Known runtime duplicate-key contract defect reproduced.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError, KeyError) as error:
        sys.exit(f"Run failed: {error}")
