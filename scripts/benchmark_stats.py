#!/usr/bin/env python3
"""Run reproducible A/B/C export-time statistics experiments.

The runner deliberately accepts a scenario manifest instead of embedding a
database name, SQL literals, credentials, or private host details.  It writes
all raw evidence below one explicit output directory and never removes paths
outside that directory.  A = unchanged/reference client, B = candidate with
stats disabled, C = candidate with --stats enabled.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def child_usage() -> tuple[float, float, int]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime, usage.ru_stime, usage.ru_maxrss


def run_checked(command: list[str], log: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    log.write_text(result.stderr, encoding="utf-8")
    if check and result.returncode:
        raise RuntimeError("command failed ({}): {}".format(
            result.returncode, " ".join(command)))
    return result


def load_scenarios(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("scenario manifest must be a non-empty JSON array")
    result = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("each scenario needs a string name")
        args = item.get("args", [])
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError("scenario args must be a string array")
        if any(arg in ("-f", "--file") or arg.startswith("--file=") for arg in args):
            raise ValueError("scenario args must not select the output path")
        result.append({"name": item["name"], "args": args})
    return result


def archive_format(args: list[str]) -> str:
    for i, arg in enumerate(args):
        if arg in ("-F", "--format") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--format="):
            return arg.split("=", 1)[1]
        if arg.startswith("-F") and len(arg) > 2:
            return arg[2:]
    return "p"


def restore_and_validate(archive: Path, fmt: str, common: list[str], ns: argparse.Namespace,
                         log: Path) -> tuple[int, str]:
    """Restore one artifact into a uniquely named disposable database."""
    database = "pgdp_bench_" + uuid.uuid4().hex[:20]
    maintenance = [ns.psql, *common]
    maintenance[maintenance.index("-d") + 1] = ns.maintenance_db
    create = subprocess.run([*maintenance, "-v", "ON_ERROR_STOP=1", "-c",
                             f'CREATE DATABASE "{database}"'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    log.write_text(create.stderr, encoding="utf-8")
    if create.returncode:
        return create.returncode, "create database failed"
    try:
        target = list(common)
        target[target.index("-d") + 1] = database
        if fmt == "p":
            restore = [ns.psql, *target, "-v", "ON_ERROR_STOP=1", "-f", str(archive)]
        else:
            restore = [ns.pg_restore, *target, "--exit-on-error", str(archive)]
        restored = subprocess.run(restore, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, check=False)
        log.write_text(log.read_text(encoding="utf-8") + restored.stderr, encoding="utf-8")
        if restored.returncode:
            return restored.returncode, "restore failed"
        if ns.validate_sql:
            checked = subprocess.run([ns.psql, *target, "-At", "-v", "ON_ERROR_STOP=1",
                                      "-c", ns.validate_sql], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            log.write_text(log.read_text(encoding="utf-8") + checked.stderr, encoding="utf-8")
            if checked.returncode:
                return checked.returncode, "validation query failed"
            return 0, checked.stdout.strip()
        return 0, ""
    finally:
        subprocess.run([*maintenance, "-v", "ON_ERROR_STOP=1", "-c",
                        f'DROP DATABASE "{database}"'], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty sample")
    pos = (len(ordered) - 1) * fraction
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def median(values: list[float]) -> float:
    return percentile(values, 0.5)


def bootstrap_delta(a: list[float], b: list[float], seed: int, rounds: int = 4000) -> tuple[float, float]:
    if len(a) != len(b) or not a:
        raise ValueError("paired bootstrap needs equal non-empty samples")
    import random
    rng = random.Random(seed)
    deltas = []
    for _ in range(rounds):
        indexes = [rng.randrange(len(a)) for _ in a]
        deltas.append(sum(b[i] - a[i] for i in indexes) / len(indexes))
    deltas.sort()
    return percentile(deltas, 0.025), percentile(deltas, 0.975)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="unchanged pg_dump binary (A)")
    parser.add_argument("--candidate", required=True, help="pg_dumpplus binary (B/C)")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--user")
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--warmup", action="store_true", help="run one untimed warm-up per label")
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--pg-restore", default="pg_restore")
    parser.add_argument("--validate-sql", help="SQL run in each disposable restore database")
    parser.add_argument("--maintenance-db", default="postgres")
    parser.add_argument("--seed", type=int, default=20260921)
    ns = parser.parse_args()
    if ns.iterations < 7:
        parser.error("--iterations must be at least 7")

    reference = Path(ns.reference).resolve()
    candidate = Path(ns.candidate).resolve()
    if not reference.is_file() or not candidate.is_file():
        parser.error("reference and candidate must be executable files")
    scenarios = load_scenarios(ns.scenarios)
    out = ns.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "stderr").mkdir()
    (out / "archives").mkdir()
    common = []
    for flag, value in (("-h", ns.host), ("-p", ns.port), ("-U", ns.user)):
        if value:
            common += [flag, value]
    common += ["-d", ns.database]
    labels = (("A", reference, False), ("B", candidate, False), ("C", candidate, True))
    records: list[dict] = []
    manifest = {
        "schema_version": 1,
        "seed": ns.seed,
        "iterations": ns.iterations,
        "database": ns.database,
        "reference": {"path": str(reference), "sha256": sha256(reference)},
        "candidate": {"path": str(candidate), "sha256": sha256(candidate)},
        "scenarios": scenarios,
        "started_at_epoch": time.time(),
        "host": ns.host,
        "port": ns.port,
        "user": ns.user,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")

    for scenario in scenarios:
        name = scenario["name"]
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        for label, binary, enabled in labels:
            if ns.warmup:
                warm = out / "stderr" / f"warmup-{safe_name}-{label}.log"
                warm_archive = out / "archives" / f"warmup-{safe_name}-{label}"
                command = [str(binary), *common, *scenario["args"]]
                if enabled:
                    command.insert(1, "--stats")
                command += ["-f", str(warm_archive)]
                run_checked(command, warm)
        # Rotate the three variants so each iteration has a different first
        # runner. This limits simple thermal/cache drift without changing the
        # paired A/B/C comparison in the summary.
        for iteration in range(1, ns.iterations + 1):
            order = labels[(iteration - 1) % len(labels):] + labels[:(iteration - 1) % len(labels)]
            for label, binary, enabled in order:
                run_id = f"{safe_name}-{label}-{iteration:02d}"
                archive = out / "archives" / run_id
                stderr = out / "stderr" / f"{run_id}.log"
                command = [str(binary), *common, *scenario["args"]]
                if enabled:
                    command.insert(1, "--stats")
                command += ["-f", str(archive)]
                before = child_usage()
                started = time.monotonic()
                result = run_checked(command, stderr, check=False)
                elapsed = time.monotonic() - started
                after = child_usage()
                row = {
                    "scenario": name, "label": label, "iteration": iteration,
                    "command": json.dumps(command), "returncode": result.returncode,
                    "elapsed_seconds": elapsed, "user_seconds": after[0] - before[0],
                    "system_seconds": after[1] - before[1],
                    "max_rss_kb": after[2], "archive_bytes": size_bytes(archive) if archive.exists() else 0,
                    "stderr_bytes": stderr.stat().st_size,
                    "archive_sha256": sha256(archive) if archive.is_file() else "",
                    "restore_returncode": "", "validation_output": "",
                }
                records.append(row)
                if result.returncode:
                    raise RuntimeError(f"{run_id} failed; see {stderr}")
                if ns.validate_sql:
                    restore_log = out / "stderr" / f"restore-{run_id}.log"
                    restore_rc, validation = restore_and_validate(
                        archive, archive_format(scenario["args"]), common, ns, restore_log)
                    row["restore_returncode"] = restore_rc
                    row["validation_output"] = validation
                    if restore_rc:
                        raise RuntimeError(f"{run_id} restore failed; see {restore_log}")

    fields = list(records[0]) if records else []
    with (out / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    summaries = []
    for scenario in scenarios:
        name = scenario["name"]
        by_label = {label: [r["elapsed_seconds"] for r in records
                            if r["scenario"] == name and r["label"] == label]
                    for label in ("A", "B", "C")}
        ci_low, ci_high = bootstrap_delta(by_label["B"], by_label["A"], ns.seed)
        c_low, c_high = bootstrap_delta(by_label["B"], by_label["C"], ns.seed + 1)
        summaries.append({
            "scenario": name,
            "A_median_seconds": median(by_label["A"]),
            "B_median_seconds": median(by_label["B"]),
            "C_median_seconds": median(by_label["C"]),
            "B_over_A_percent": 100 * (median(by_label["B"]) - median(by_label["A"])) / median(by_label["A"]),
            "C_over_B_percent": 100 * (median(by_label["C"]) - median(by_label["B"])) / median(by_label["B"]),
            "B_minus_A_ci95_seconds": [ci_low, ci_high],
            "C_minus_B_ci95_seconds": [c_low, c_high],
        })
    (out / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} raw runs and {len(summaries)} summaries to {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"benchmark_stats: {exc}", file=sys.stderr)
        raise SystemExit(1)
