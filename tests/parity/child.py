"""Run one parity plan against a given source tree, with time frozen.

Started by driver.py as `python -I -B child.py` with the plan as JSON on stdin.
It imports `ai_usage_service` and `ai_usage_report` from the plan's `src`
directory, freezes wall-clock time in every loaded `ai_usage*` module and in
`logging`, runs the plan's steps in order, and prints one JSON document.

Only the public entry points both the pre-refactor and the refactored code
share are used, so the same plan runs unchanged against either.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as real_datetime
import io
import json
import logging
import os
from pathlib import Path
import sys
import time as real_time
import types
from typing import Any


def frozen_modules(fixed_ts: float) -> tuple[types.ModuleType, types.ModuleType]:
    fixed_utc = real_datetime.datetime.fromtimestamp(fixed_ts, tz=real_datetime.timezone.utc)

    class FrozenDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            value = fixed_utc.astimezone(tz) if tz is not None else fixed_utc.astimezone().replace(tzinfo=None)
            return cls.combine(value.date(), value.timetz())

        @classmethod
        def utcnow(cls):  # type: ignore[override]
            return cls.combine(fixed_utc.date(), fixed_utc.time())

        @classmethod
        def today(cls):  # type: ignore[override]
            return cls.now()

    datetime_shim = types.ModuleType("datetime")
    datetime_shim.__dict__.update(real_datetime.__dict__)
    datetime_shim.datetime = FrozenDateTime  # type: ignore[attr-defined]

    time_shim = types.ModuleType("time")
    time_shim.__dict__.update(real_time.__dict__)
    time_shim.time = lambda: fixed_ts  # type: ignore[attr-defined]
    time_shim.time_ns = lambda: int(fixed_ts * 1_000_000_000)  # type: ignore[attr-defined]
    return datetime_shim, time_shim


def freeze(fixed_ts: float) -> None:
    datetime_shim, time_shim = frozen_modules(fixed_ts)
    for name, module in list(sys.modules.items()):
        if not (name.startswith("ai_usage") and isinstance(module, types.ModuleType)):
            continue
        for attribute, value in list(vars(module).items()):
            if value is real_datetime:
                setattr(module, attribute, datetime_shim)
            elif value is real_time:
                setattr(module, attribute, time_shim)
            elif value is real_datetime.datetime:
                setattr(module, attribute, datetime_shim.datetime)
    # Log lines carry their own timestamp; the report reads them back.
    logging.time = time_shim  # type: ignore[attr-defined]


def read_csv(path: Path) -> Any:
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def captured(call: Any) -> dict[str, Any]:
    stdout, stderr = io.StringIO(), io.StringIO()
    result: dict[str, Any] = {}
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = call()
        result["return"] = value
    except SystemExit as exit_:
        result["exit"] = exit_.code
    except Exception as error:  # Recorded, never raised: errors are part of parity.
        result["error"] = f"{type(error).__name__}: {error}"
    result["stdout"] = stdout.getvalue()
    result["stderr"] = stderr.getvalue()
    return result


def main() -> int:
    plan = json.loads(sys.stdin.read())
    sys.path.insert(0, plan["src"])
    import ai_usage_service as service  # noqa: E402
    import ai_usage_report as report  # noqa: E402

    freeze(float(plan["now"]))
    config_path = Path(plan["config"])
    results: list[dict[str, Any]] = []
    for step in plan["steps"]:
        op = step["op"]
        if op == "default_config":
            # The text keeps key order: install writes it as the new config.json.
            results.append({"op": op, "value": json.loads(json.dumps(service.DEFAULT_CONFIG)),
                            "text": json.dumps(service.DEFAULT_CONFIG, indent=2)})
        elif op == "doctor_json":
            results.append({"op": op, **captured(lambda: service.main(["doctor", "--json", "--config", str(config_path)]))})
        elif op == "doctor_text":
            results.append({"op": op, **captured(lambda: service.main(["doctor", "--config", str(config_path)]))})
        elif op == "once":
            arguments = ["once", "--config", str(config_path)] + (["--progress"] if step.get("progress") else [])
            outcome = captured(lambda: service.main(arguments))
            paths = step["paths"]
            outcome["csv"] = read_csv(Path(paths["usage_csv"]))
            outcome["state"] = read_json(Path(paths["state_file"]))
            outcome["pending"] = Path(paths["state_file"] + ".pending").exists()
            results.append({"op": op, **outcome})
        elif op == "statusline":
            stdin = sys.stdin
            sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(step["payload"]).encode("utf-8")), encoding="utf-8")
            try:
                outcome = captured(
                    lambda: service.main(["antigravity-statusline", "--config", str(config_path)])
                )
            finally:
                sys.stdin = stdin
            outcome["cache"] = read_json(Path(step["cache_file"]))
            events = Path(step["events_file"])
            outcome["events"] = events.read_text(encoding="utf-8") if events.is_file() else None
            results.append({"op": op, **outcome})
        elif op == "append":
            for path, text in step["files"].items():
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(text)
            for path, mtime in step.get("mtimes", {}).items():
                os.utime(path, (mtime, mtime))
            results.append({"op": op})
        elif op == "configure":
            target = Path(step["path"])
            outcome = captured(lambda: service.configure(target, step["assignments"]))
            outcome["is_symlink"] = target.is_symlink()
            outcome["text"] = target.read_text(encoding="utf-8") if target.exists() else None
            results.append({"op": op, **outcome})
        elif op == "load_config":
            outcome = captured(lambda: json.loads(json.dumps(service.load_config(Path(step["path"])))))
            results.append({"op": op, **outcome})
        elif op == "report":
            arguments = [
                "--config", str(config_path), "--now", plan["now_iso"], "--service", "skip",
                "--installed-collector", str(Path(plan["src"]) / "ai_usage_service.py"),
                "--days", str(step.get("days", 30)),
            ]
            outcome = captured(lambda: report.main(arguments))
            try:
                outcome["report"] = json.loads(outcome.pop("stdout"))
            except ValueError:
                outcome["report"] = None
            results.append({"op": op, **outcome})
        else:
            raise SystemExit(f"unknown parity step: {op}")
    sys.__stdout__.write(json.dumps({"results": results}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
