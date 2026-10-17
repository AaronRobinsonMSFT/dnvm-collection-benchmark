# Repository guidance

Compare native runtime collections with STL and Abseil across Windows x64/x86,
macOS arm64, and Linux GCC/Clang. Collect CPU and memory results.

## Changes

- Keep changes focused and preserve user edits.
- Use C11, C++17, and standard-library Python. Follow existing code patterns.
- Keep dependencies pinned and shallow. Do not edit submodule sources or change
  their revisions without approval.
- Keep the README to repository setup, platform options, collection commands,
  and result locations. Use brief, direct prose; omit general GitHub usage.
- Keep shared guidance in this file; harness-specific files should only refer
  to or import it.

## Validation and results

- Use the build and run commands in `readme.md`.
- Run functional validation once each on Linux GCC and Clang. Windows and macOS
  collect measurements only.
- Measurement builds use Release with `BUILD_TESTING=OFF`. Keep memory accounting
  separate from CPU timing.
- For Python changes, run
  `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Keep raw results and provenance. Compare implementations within each run,
  not across different machines.
- Producers own workload descriptions and inventories; reporters consume schemas,
  not collection names or test definitions.
- Use the `Collections` workflow to verify platform changes. Report actual
  outcomes; do not describe unrun jobs as passing.

## Git

- Do not commit, amend, or push without explicit user approval.
