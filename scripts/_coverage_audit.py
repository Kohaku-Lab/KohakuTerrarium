"""Cross-tier per-file coverage audit.

Usage: python scripts/_coverage_audit.py

Runs each test tier with coverage and reports files with low coverage
in each tier so the next-test-to-write decision can be data-driven.
Temporary scaffolding — delete after the coverage push lands.
"""

import subprocess
import sys


def file_cov(suite_args: list[str]) -> dict[str, tuple[int, int]]:
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *suite_args,
            "--cov=src/kohakuterrarium",
            "--cov-report=term",
            "--no-header",
            "-q",
            "--timeout=30",
            "--tb=no",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    out: dict[str, tuple[int, int]] = {}
    for line in r.stdout.split("\n"):
        if not line.startswith("src"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            stmts = int(parts[1])
            miss = int(parts[2])
        except Exception:
            continue
        rel = parts[0].split("kohakuterrarium", 1)[1].lstrip("\\/")
        rel = rel.replace("\\", "/")
        out[rel] = (stmts, miss)
    return out


def main() -> None:
    print("Running unit...")
    u = file_cov(["tests/unit", "--ignore=tests/unit/test_litellm_provider.py"])
    print(f"  unit: {len(u)} files")
    print("Running integration...")
    i = file_cov(["tests/integration"])
    print(f"  integration: {len(i)} files")
    print("Running e2e...")
    e = file_cov(["tests/e2e"])
    print(f"  e2e: {len(e)} files")

    candidates = []
    for path, (s_u, m_u) in u.items():
        if s_u < 40:
            continue
        # Skip UI/CLI tools that the user accepts as low-coverage.
        if any(
            p in path for p in ("tui/", "cli_rich/", "cli/", "_briefcase", "web_dist")
        ):
            continue
        pct_u = (s_u - m_u) / s_u
        s_i, m_i = i.get(path, (s_u, s_u))
        s_e, m_e = e.get(path, (s_u, s_u))
        pct_i = (s_i - m_i) / s_i if s_i else 0.0
        pct_e = (s_e - m_e) / s_e if s_e else 0.0
        max_pct = max(pct_u, pct_i, pct_e)
        if max_pct < 0.60:
            candidates.append((path, s_u, m_u, pct_u, pct_i, pct_e, max_pct))

    candidates.sort(key=lambda x: -x[2])
    print()
    print(f"{'path':<60s}  stmts  miss   u%   i%   e%  best%")
    print("-" * 95)
    for path, s, m, pu, pi, pe, mx in candidates[:50]:
        print(
            f"{path:<60s} {s:5d} {m:5d}  "
            f"{pu * 100:3.0f}  {pi * 100:3.0f}  {pe * 100:3.0f}  {mx * 100:3.0f}"
        )


if __name__ == "__main__":
    main()
