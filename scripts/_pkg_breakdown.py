"""Per-package coverage breakdown from the combined data file."""

import subprocess


def main() -> None:
    r = subprocess.run(
        ["python", "-m", "coverage", "report", "--include=src/kohakuterrarium/*"],
        capture_output=True,
        text=True,
    )
    agg: dict[str, list[int]] = {}
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
        pkg = rel.split("/")[0] if "/" in rel else "TOP"
        if ".py" in pkg:
            pkg = "TOP"
        a = agg.setdefault(pkg, [0, 0])
        a[0] += stmts
        a[1] += miss

    print(f"{'package':<20s}  stmts  miss    cov")
    print("-" * 45)
    for pkg, (s, m) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        cov = int((s - m) * 100 / s) if s else 0
        print(f"{pkg:<20s} {s:6d} {m:6d}  {cov:3d}%")
    total_s = sum(s for s, _ in agg.values())
    total_m = sum(m for _, m in agg.values())
    print("-" * 45)
    pct = int((total_s - total_m) * 100 / total_s) if total_s else 0
    print(f"{'TOTAL':<20s} {total_s:6d} {total_m:6d}  {pct:3d}%")


if __name__ == "__main__":
    main()
