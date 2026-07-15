"""``kt doctor`` — pre-flight environment + config validation.

Thin CLI wrapper over :mod:`kohakuterrarium.validate`: same checks,
raise-instead-of-warn semantics, rendered as a ✓/✗ report.  Exit code
is 0 only when every executed check passed.
"""

import asyncio

from kohakuterrarium import validate
from kohakuterrarium.cli.shims import shims_summary
from kohakuterrarium.llm.profiles import get_default_model
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _check(label: str, fn) -> tuple[bool, str]:
    """Run one check; return (ok, detail)."""
    try:
        out = fn()
        return True, str(out) if out is not None else ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def doctor_cli(
    target: str | None = None,
    *,
    llm: str | None = None,
    ping: bool = False,
) -> int:
    """Run the doctor report.

    Args:
        target: Optional creature config folder / ``@pkg`` ref to
            dry-run build with full strictness.
        llm: Optional model selector to validate (defaults to the
            configured default model).
        ping: Also make one real LLM round-trip (network).
    """
    results: list[tuple[str, bool, str]] = []

    def run(label: str, fn) -> bool:
        """Execute and record one doctor check."""
        ok, detail = _check(label, fn)
        results.append((label, ok, detail))
        return ok

    def _config_dir():
        """Verify that the configuration directory is writable."""
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".kt-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return d

    run("config dir writable", _config_dir)

    run(
        "installed packages",
        lambda: ", ".join(p.get("name", "?") for p in list_packages()) or "(none)",
    )

    # Shim status is informational and never raises for missing installations.
    run("terminal shims", shims_summary)

    selector = llm or get_default_model() or None
    if selector or llm is None:
        run(
            f"llm resolves ({selector or 'default'})",
            lambda: validate.llm(selector),
        )

    if target:

        def _build():
            """Validate and summarize the requested creature."""
            report = validate.creature(target)
            return (
                f"{report.name} [model={report.model_identifier}, "
                f"{len(report.tools)} tools, {len(report.plugins)} plugins]"
            )

        run(f"creature builds ({target})", _build)

    if ping:
        run(
            f"llm ping ({selector or 'default'})",
            lambda: asyncio.run(validate.ping(selector)),
        )

    width = max(len(label) for label, _ok, _d in results)
    all_ok = True
    for label, ok, detail in results:
        mark = "✓" if ok else "✗"
        line = f" {mark} {label.ljust(width)}"
        if detail:
            line += f"  {detail}"
        print(line)
        all_ok = all_ok and ok

    print()
    print("All checks passed." if all_ok else "Some checks FAILED — see above.")
    return 0 if all_ok else 1


def add_doctor_subparser(subparsers) -> None:
    """Register the ``doctor`` subcommand on the main parser."""
    p = subparsers.add_parser(
        "doctor",
        help="Validate your setup (config dir, packages, model, creature)",
    )
    p.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Optional creature config folder or @pkg/... ref to dry-run build",
    )
    p.add_argument(
        "--llm",
        default=None,
        help="Model selector to validate (defaults to the configured default)",
    )
    p.add_argument(
        "--ping",
        action="store_true",
        help="Also make one real (billed) LLM round-trip",
    )


def dispatch_doctor(args) -> int:
    """Dispatch parsed doctor arguments."""
    return doctor_cli(args.target, llm=args.llm, ping=args.ping)
