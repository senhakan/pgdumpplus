#!/usr/bin/env python3
"""Verify a dump client using small, disposable, uniquely named databases.

Requires Python 3 and PostgreSQL client tools. Connection settings come from
PGHOST/PGPORT/PGUSER/PGPASSWORD/PGPASSFILE. Run as a role with CREATEDB.
Only databases successfully created by this invocation are removed. No FORCE
drop, existing database reuse, or access to application tables is performed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import uuid


FIXTURE = """
CREATE DOMAIN masked_label AS text CHECK (length(VALUE) < 100);
CREATE FUNCTION pgdp_pause_true() RETURNS boolean
LANGUAGE plpgsql VOLATILE AS $$BEGIN PERFORM pg_sleep(0.0001); RETURN true; END$$;
CREATE TABLE customers (
    id integer PRIMARY KEY, full_name text, ssn varchar(11), phone text,
    email text, address text, iban text, card_number text, uuid_value text,
    native_uuid uuid,
    birth_year integer,
    doubled integer GENERATED ALWAYS AS (id * 2) STORED
);
INSERT INTO customers (id, full_name, ssn, phone, email, address, iban,
                       card_number, uuid_value, birth_year, native_uuid)
SELECT g, 'Customer ' || g, '12345678901', '05551234567',
       'user' || g || '@example.com', 'Address ' || g,
       'DE89370400440532013000', '4111111111111111',
       '550e8400-e29b-41d4-a716-446655440000', 1980 + g % 30,
       '550e8400-e29b-41d4-a716-446655440000'::uuid
FROM generate_series(1,100) g;
CREATE TABLE domain_examples (id integer PRIMARY KEY, label masked_label);
INSERT INTO domain_examples
SELECT g, ('Ürün  ' || g)::masked_label FROM generate_series(1,3) g;
CREATE TABLE snapshot_probe (id integer PRIMARY KEY, state text NOT NULL);
INSERT INTO snapshot_probe
SELECT g, 'before' FROM generate_series(1,20000) g;
CREATE TABLE orders (
    id integer PRIMARY KEY, customer_id integer REFERENCES customers(id),
    payload text NOT NULL
);
INSERT INTO orders SELECT g, (g - 1) % 100 + 1, 'payload-' || g
FROM generate_series(1,1000) g;
CREATE TABLE "Odd:Table" (id integer PRIMARY KEY, "Secret:Value" text,
    "A""B" text, "select" text);
INSERT INTO "Odd:Table" VALUES
    (1, 'private-value', 'quoted', 'keyword'),
    (2, 'another-value', 'quoted', 'keyword');
CREATE TABLE mask_edges (id integer PRIMARY KEY, value text);
INSERT INTO mask_edges VALUES
    (1, NULL), (2, ''), (3, '1'), (4, '12'), (5, '123'), (6, '1234'), (7, '12345');
CREATE TABLE stats_probe (id integer, payload text);
INSERT INTO stats_probe SELECT g, 'row-' || g FROM generate_series(1,7) g;
CREATE TABLE stats_zero (id integer);
ALTER TABLE stats_zero DROP COLUMN id;
INSERT INTO stats_zero DEFAULT VALUES;
INSERT INTO stats_zero DEFAULT VALUES;
CREATE TABLE partitioned_events (id integer, secret text) PARTITION BY RANGE (id);
CREATE TABLE partitioned_events_1 PARTITION OF partitioned_events FOR VALUES FROM (1) TO (4);
INSERT INTO partitioned_events VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma');
"""


class Suite:
    def __init__(self, args, work):
        self.args = args
        self.work = Path(work)
        self.env = dict(os.environ, PGCONNECT_TIMEOUT="10")
        suffix = uuid.uuid4().hex[:16]
        self.source = "pgdp_verify_" + suffix + "_src"
        self.target = "pgdp_verify_" + suffix + "_dst"
        self.created = []
        self.results = []
        self.serial = 0

    def run(self, argv, check=True):
        result = subprocess.run(
            [str(v) for v in argv], env=self.env, universal_newlines=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        if check and result.returncode:
            raise RuntimeError("{} exited {}: {}".format(
                Path(str(argv[0])).name, result.returncode, result.stderr.strip()))
        return result

    def sql(self, database, query):
        return self.run([
            self.args.psql, "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
            "-d", database, "-c", query,
        ]).stdout.strip()

    def setup(self):
        for database in (self.source, self.target):
            self.sql(self.args.maintenance_db, 'CREATE DATABASE "{}" TEMPLATE template0'.format(database))
            self.created.append(database)
        self.sql(self.source, FIXTURE)

    def cleanup(self):
        errors = []
        for database in reversed(self.created):
            try:
                self.sql(self.args.maintenance_db, 'DROP DATABASE "{}"'.format(database))
            except Exception as exc:
                errors.append("{}: {}".format(database, exc))
        if errors:
            raise RuntimeError("Cleanup failed; remove only these test databases: " + "; ".join(errors))

    def dump(self, options=(), fmt="c", binary=None, check=True):
        self.serial += 1
        path = self.work / ("dump_" + str(self.serial))
        result = self.run([
            binary or self.args.binary, "-d", self.source, "--no-owner",
            "--no-privileges", "--lock-wait-timeout=10s", "-F", fmt,
            *options, "-f", path,
        ], check=check)
        return path, result

    def restore(self, path, fmt="c"):
        # self.target was created successfully by this run, never supplied by a user.
        self.sql(self.target, "DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        if fmt == "p":
            self.run([self.args.psql, "-X", "-q", "-v", "ON_ERROR_STOP=1",
                      "-d", self.target, "-f", path])
        else:
            self.run([self.args.pg_restore, "--exit-on-error", "--no-owner",
                      "--no-privileges", "-d", self.target, path])

    @staticmethod
    def equal(actual, expected):
        if actual != expected:
            raise AssertionError("expected {!r}, got {!r}".format(expected, actual))

    def case(self, name, action):
        try:
            action()
        except Exception as exc:
            self.results.append({"name": name, "passed": False, "detail": str(exc)})
            print("FAIL {}: {}".format(name, exc), flush=True)
        else:
            self.results.append({"name": name, "passed": True})
            print("PASS " + name, flush=True)

    def roundtrip(self, options, query, expected, fmt="c"):
        path, _ = self.dump(options, fmt)
        self.restore(path, fmt)
        self.equal(self.sql(self.target, query), expected)

    def error(self, option, fragment):
        _, result = self.dump([option], check=False)
        if result.returncode == 0 or fragment not in result.stderr:
            raise AssertionError("expected failure containing {!r}; exit={}, stderr={}".format(
                fragment, result.returncode, result.stderr.strip()))

    def unfiltered(self):
        normal, _ = self.dump(fmt="p", binary=self.args.vanilla)
        plus, _ = self.dump(fmt="p")
        # Recent PostgreSQL clients use a different random psql guard per dump.
        def normalize(path):
            return re.sub(r"^\\(?:un)?restrict .*$", "", path.read_text(), flags=re.M)
        self.equal(normalize(plus), normalize(normal))
        # Keep the schema-only path aligned with the matching vanilla client;
        # this is an upstream-regression check, not just a data round trip.
        normal_schema, _ = self.dump(["--schema-only"], fmt="p", binary=self.args.vanilla)
        plus_schema, _ = self.dump(["--schema-only"], fmt="p")
        self.equal(normalize(plus_schema), normalize(normal_schema))
        self.restore(plus, "p")
        self.equal(self.sql(self.target, "SELECT count(*) FROM orders"), "1000")

    def invalid_mask(self):
        _, result = self.dump(["--mask=public.customers:missing_column:all"], check=False)
        if result.returncode == 0 or "not found" not in result.stderr:
            raise AssertionError("missing-column mask did not fail")

    def invalid_mask_selection(self):
        _, result = self.dump([
            "-t", "public.orders", "--mask=public.customers:ssn:all",
        ], check=False)
        if result.returncode == 0 or "not selected" not in result.stderr:
            raise AssertionError("mask on excluded table did not fail")

    def duplicate_mask(self):
        _, result = self.dump([
            "--mask=public.customers:ssn:all",
            "--mask=public.customers:ssn:identity",
        ], check=False)
        if result.returncode == 0 or "duplicate" not in result.stderr:
            raise AssertionError("duplicate mask did not fail")

    def partition_mask_selection_error(self):
        _, result = self.dump([
            "-t", "public.partitioned_events",
            "--mask=public.partitioned_events_1:secret:all",
        ], check=False)
        if result.returncode == 0 or "not selected" not in result.stderr:
            raise AssertionError("partition child mask did not fail explicitly")

    def dry_run(self, plan_format="text"):
        result = self.run([
            self.args.binary, "-d", self.source, "--no-owner", "--no-privileges",
            "--dry-run", "--plan-format=" + plan_format,
            "--mask=public.customers:ssn:identity",
            "--where=public.orders:id <= 25",
        ])
        if plan_format == "json":
            plan = json.loads(result.stdout)
            if plan.get("schema_version") != 1 or len(plan.get("masks", [])) != 1:
                raise AssertionError("invalid dry-run JSON plan: " + result.stdout)
        elif "pg_dumpplus dry-run plan" not in result.stdout or "column=ssn" not in result.stdout:
            raise AssertionError("invalid dry-run text plan: " + result.stdout)

    def dry_run_quoted_json(self):
        result = self.run([
            self.args.binary, "-d", self.source, "--no-owner", "--no-privileges",
            "--dry-run", "--plan-format=json",
            '--mask=public."Odd:Table":"Secret:Value":all',
        ])
        plan = json.loads(result.stdout)
        if plan["masks"][0]["table"] != "Odd:Table" or plan["masks"][0]["column"] != "Secret:Value":
            raise AssertionError("quoted identifier was not escaped in JSON plan")

    def dry_run_does_not_execute_custom_sql(self):
        result = self.run([
            self.args.binary, "-d", self.source, "--no-owner", "--no-privileges",
            "--dry-run", "--plan-format=json",
            "--mask=public.customers:email:current_setting('pgdp_missing_setting')",
        ])
        plan = json.loads(result.stdout)
        if plan["masks"][0]["mask"] != "custom":
            raise AssertionError("custom expression was not identified in dry-run plan")

    def dry_run_option_errors(self):
        path = self.work / "dry-run-invalid.dump"
        for options, fragment in (
            (["--dry-run", "-f", path], "cannot be used"),
            (["--plan-format=json"], "requires --dry-run"),
            (["--schema-only", "--mask=public.customers:ssn:identity"], "schema-only"),
        ):
            result = self.run([self.args.binary, "-d", self.source, *options], check=False)
            if result.returncode == 0 or fragment not in result.stderr:
                raise AssertionError("expected dry-run option failure: " + result.stderr)

    def profile_roundtrip(self):
        """The compiled client must load the checked-in JSON profile directly."""
        profile = Path(__file__).resolve().parents[1] / "docs/examples/profiles/support-extract.json"
        result = self.run([
            self.args.binary, "-d", self.source, "--no-owner", "--no-privileges",
            "--dry-run", "--plan-format=json", "--profile", profile,
        ])
        plan = json.loads(result.stdout)
        masks = plan.get("masks", [])
        if len(masks) != 2:
            raise AssertionError("compiled profile was not resolved in the catalog plan: " + result.stdout)
        path, _ = self.dump(["--profile", profile], fmt="p")
        self.restore(path, "p")
        self.equal(self.sql(self.target, "SELECT count(*) FROM orders"), "25")
        self.equal(self.sql(self.target, "SELECT phone FROM customers WHERE id=1"), "********567")
        invalid = Path(__file__).resolve().parents[1] / "docs/design/profile-schema.json"
        rejected = self.run([
            self.args.binary, "-d", self.source, "--profile", invalid,
        ], check=False)
        if rejected.returncode == 0 or "invalid --profile" not in rejected.stderr:
            raise AssertionError("compiled profile parser accepted an invalid schema document")

    def snapshot_consistency(self):
        """A concurrent committed update must not produce mixed dump values."""
        for fmt in ("p", "c", "d"):
            self.sql(self.source, "UPDATE snapshot_probe SET state = 'before'")

            def writer():
                self.sql(self.source, "UPDATE snapshot_probe SET state = 'after'")

            timer = threading.Timer(0.25, writer)
            timer.start()
            try:
                path, result = self.dump([
                    "--where=public.snapshot_probe:id > 0 AND public.pgdp_pause_true()",
                ], fmt=fmt)
                if result.returncode:
                    raise RuntimeError(result.stderr)
            finally:
                timer.join(timeout=30)
                if timer.is_alive():
                    raise RuntimeError("snapshot writer did not finish")
            self.restore(path, fmt)
            self.equal(self.sql(self.target,
                                "SELECT count(*), count(DISTINCT state) FROM snapshot_probe"),
                       "20000|1")

    def stats_copy(self):
        """--stats reports the server COPY count and serialized payload bytes."""
        _, result = self.dump(["--stats", "-t", "public.stats_probe"])
        match = re.search(
            r'table "public\.stats_probe": rows=(\d+), size=(\d+) B, duration=[^\n]+',
            result.stderr,
        )
        if not match:
            raise AssertionError("missing COPY stats line: " + result.stderr)
        self.equal(match.group(1), "7")
        expected = sum(len((f"{i}\trow-{i}\n").encode("utf-8")) for i in range(1, 8))
        self.equal(int(match.group(2)), expected)

        _, filtered = self.dump([
            "--stats", "-t", "public.stats_probe",
            "--where=public.stats_probe:id % 2 = 0",
        ], fmt="p")
        filtered_match = re.search(
            r'table "public\.stats_probe": rows=(\d+), size=(\d+) B, duration=[^\n]+',
            filtered.stderr,
        )
        if not filtered_match:
            raise AssertionError("missing filtered stats line: " + filtered.stderr)
        self.equal(filtered_match.group(1), "3")
        expected_filtered = sum(
            len((f"{i}\trow-{i}\n").encode("utf-8")) for i in (2, 4, 6)
        )
        self.equal(int(filtered_match.group(2)), expected_filtered)

    def stats_run_summary(self):
        """--stats emits one run banner and a successful whole-export summary."""
        _, result = self.dump(["--stats", "-t", "public.stats_probe"], fmt="p")
        start = re.search(
            r"pg_dumpplus: export started: "
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}; "
            r"database server version: [^\n]+",
            result.stderr,
        )
        completed = re.search(
            r"pg_dumpplus: export completed: "
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}; "
            r"elapsed: (?:\d+ms|\d+s|\d+m\d+s|\d+h\d+m\d+s)",
            result.stderr,
        )
        table = re.search(r'table "public\.stats_probe":', result.stderr)
        if not start or not completed or not table:
            raise AssertionError("missing --stats run summary: " + result.stderr)
        if not (start.start() < table.start() < completed.start()):
            raise AssertionError("--stats run summary has unexpected order: " + result.stderr)

        _, without_stats = self.dump(["-t", "public.stats_probe"], fmt="p")
        if "export started:" in without_stats.stderr or "export completed:" in without_stats.stderr:
            raise AssertionError("run summary appeared without --stats: " + without_stats.stderr)

    def stats_insert(self):
        """--stats counts INSERT tuples and emitted SQL bytes independently."""
        path, result = self.dump([
            "--stats", "--inserts", "--rows-per-insert=1", "-t", "public.stats_probe",
        ], fmt="p")
        match = re.search(
            r'table "public\.stats_probe": rows=(\d+), size=(\d+) B, duration=[^\n]+',
            result.stderr,
        )
        if not match:
            raise AssertionError("missing INSERT stats line: " + result.stderr)
        self.equal(match.group(1), "7")
        expected = sum(
            len((f"INSERT INTO public.stats_probe VALUES ({i}, 'row-{i}');\n").encode())
            for i in range(1, 8)
        ) + 2  # the existing INSERT data callback terminator (two newlines)
        self.equal(int(match.group(2)), expected)
        self.restore(path, "p")
        self.equal(self.sql(self.target, "SELECT count(*) FROM stats_probe"), "7")

        _, column_result = self.dump([
            "--stats", "--inserts", "--column-inserts", "--rows-per-insert=3",
            "-t", "public.stats_probe",
        ], fmt="p")
        column_match = re.search(
            r'table "public\.stats_probe": rows=(\d+), size=(\d+) B, duration=[^\n]+',
            column_result.stderr,
        )
        if not column_match:
            raise AssertionError("missing column INSERT stats line: " + column_result.stderr)
        self.equal(column_match.group(1), "7")
        expected_column = 0
        for start in (1, 4, 7):
            rows = range(start, min(start + 3, 8))
            expected_column += len(
                ("INSERT INTO public.stats_probe (id, payload) VALUES\n" +
                 ",\n".join(f"\t({i}, 'row-{i}')" for i in rows) + ";\n").encode()
            )
        self.equal(int(column_match.group(2)), expected_column + 2)

        _, zero_result = self.dump([
            "--stats", "--inserts", "-t", "public.stats_zero",
        ], fmt="p")
        zero_match = re.search(
            r'table "public\.stats_zero": rows=(\d+), size=(\d+) B, duration=[^\n]+',
            zero_result.stderr,
        )
        if not zero_match:
            raise AssertionError("missing DEFAULT VALUES stats line: " + zero_result.stderr)
        self.equal(zero_match.group(1), "2")
        self.equal(int(zero_match.group(2)),
                   2 * len(b"INSERT INTO public.stats_zero DEFAULT VALUES;\n") + 2)

    def checks(self):
        self.case("unfiltered dump matches upstream (random guards normalized)", self.unfiltered)
        for fmt, extra in (("c", []), ("p", []), ("p", ["--inserts"]),
                           ("p", ["--column-inserts"]), ("d", ["-j", "2"])):
            label = fmt + (" " + " ".join(extra) if extra else "")
            self.case("filter + multi-mask roundtrip " + label, lambda fmt=fmt, extra=extra:
                self.roundtrip([
                    "--where=public.orders:id <= 25",
                    "--mask=public.customers:ssn:identity",
                    "--mask=public.customers:phone:phone",
                    "--mask=public.customers:full_name:all", *extra,
                ], "SELECT (SELECT count(*) FROM orders), (SELECT count(*) FROM customers), "
                   "ssn, phone, full_name, doubled FROM customers WHERE id=1",
                   "25|100|12*******01|********567|**********|2", fmt))
        self.case("FK-consistent parent and child filters", lambda: self.roundtrip([
            "--where=public.customers:id <= 10", "--where=public.orders:customer_id <= 10",
        ], "SELECT (SELECT count(*) FROM customers), count(*), "
           "count(*) FILTER (WHERE c.id IS NULL) FROM orders o LEFT JOIN customers c ON c.id=o.customer_id",
           "10|100|0"))
        self.case("wildcard filter", lambda: self.roundtrip([
            "--where=public.ord*:id <= 12",
        ], "SELECT count(*) FROM orders", "12"))
        self.case("quoted table and column filter", lambda: self.roundtrip([
            '--where=public."Odd:Table":"Secret:Value" = \'private-value\'',
        ], 'SELECT count(*) FROM "Odd:Table"', "1"))
        self.case("table selection and filter", lambda: self.roundtrip([
            "-t", "public.customers", "--where=public.customers:id <= 3",
        ], "SELECT count(*) FROM customers", "3"))
        self.case("partition child mask selection is explicit", self.partition_mask_selection_error)
        self.case("custom text and integer masks", lambda: self.roundtrip([
            "--mask=public.customers:full_name:upper(full_name)",
            "--mask=public.customers:birth_year:2000",
        ], "SELECT full_name, birth_year FROM customers WHERE id=2", "CUSTOMER 2|2000"))
        self.case("custom mask on text domain", lambda: self.roundtrip([
            "--mask=public.domain_examples:label:upper(label)::public.masked_label",
        ], "SELECT label FROM domain_examples WHERE id=1", "ÜRÜN  1"))
        self.case("missing mask column fails before export", self.invalid_mask)
        self.case("mask on excluded table fails before export", self.invalid_mask_selection)
        self.case("duplicate mask fails before export", self.duplicate_mask)
        self.case("catalog-only dry-run text plan", self.dry_run)
        self.case("catalog-only dry-run JSON plan", lambda: self.dry_run("json"))
        self.case("dry-run JSON quoted identifiers", self.dry_run_quoted_json)
        self.case("dry-run does not execute custom SQL", self.dry_run_does_not_execute_custom_sql)
        self.case("dry-run option combinations fail clearly", self.dry_run_option_errors)
        self.case("compiled JSON profile resolves and restores", self.profile_roundtrip)
        self.case("COPY export stats count rows and bytes", self.stats_copy)
        self.case("--stats export start and completion summary", self.stats_run_summary)
        self.case("INSERT export stats count tuples and SQL bytes", self.stats_insert)
        self.case("concurrent update keeps one dump snapshot", self.snapshot_consistency)
        self.case("preset on non-text column fails before export", lambda: self.error(
            "--mask=public.customers:birth_year:all", "yields text"))
        self.case("mask on generated column fails before export", lambda: self.error(
            "--mask=public.customers:doubled:all", "generated"))
        self.case("text preset rejects native UUID", lambda: self.error(
            "--mask=public.customers:native_uuid:all", "yields text"))
        self.case("text preset rejects domain without base-type proof", lambda: self.error(
            "--mask=public.domain_examples:label:all", "yields text"))
        for name, option, fragment in (
            ("where separator", "--where=public.orders", "missing"),
            ("where table", "--where=public.no_such_table:id=1", "no matching"),
            ("where SQL", "--where=public.orders:no_such_column=1", "does not exist"),
            ("mask separator", "--mask=public.customers:ssn", "missing"),
            ("mask table", "--mask=public.no_such_table:ssn:all", "no matching"),
            ("mask SQL", "--mask=public.customers:ssn:no_such_function(ssn)", "does not exist"),
            ("qualified mask column", "--mask=public.customers:customers.ssn:all", "single SQL identifier"),
        ):
            self.case("error: " + name, lambda option=option, fragment=fragment: self.error(option, fragment))
        self.case("quoted mask column", lambda: self.roundtrip([
            '--mask=public."Odd:Table":"Secret:Value":all',
        ], 'SELECT "Secret:Value" FROM "Odd:Table" WHERE id=1', "*************"))
        self.case("escaped identifier and keyword masks with column inserts", lambda: self.roundtrip([
            '--mask=public."Odd:Table":"A""B":all',
            '--mask=public."Odd:Table":"select":all', "--column-inserts",
        ], 'SELECT "A""B", "select" FROM "Odd:Table" WHERE id=1', "******|*******", "p"))
        self.case("unquoted mask identifier folds case", lambda: self.roundtrip([
            "--mask=public.customers:FULL_NAME:all",
        ], "SELECT full_name FROM customers WHERE id=1", "**********"))
        self.case("all preset preserves NULL", lambda: self.roundtrip([
            "--mask=public.mask_edges:value:all",
        ], "SELECT value IS NULL FROM mask_edges WHERE id=1", "t"))
        self.case("identity preset preserves short-value length", lambda: self.roundtrip([
            "--mask=public.mask_edges:value:identity",
        ], "SELECT string_agg(length(value)::text, ',' ORDER BY id) FROM mask_edges WHERE id >= 2",
           "0,1,2,3,4,5"))
        self.case("identity preset masks all characters in short values", lambda: self.roundtrip([
            "--mask=public.mask_edges:value:identity",
        ], "SELECT string_agg(value, ',' ORDER BY id) FROM mask_edges WHERE id >= 2",
           ",*,**,***,****,12*45"))
        self.case("all preset preserves empty string", lambda: self.roundtrip([
            "--mask=public.mask_edges:value:all",
        ], "SELECT length(value) FROM mask_edges WHERE id=2", "0"))
        self.case("market presets", lambda: self.roundtrip([
            "--mask=public.customers:email:email",
            "--mask=public.customers:full_name:name",
            "--mask=public.customers:address:address",
            "--mask=public.customers:iban:iban",
            "--mask=public.customers:card_number:card",
            "--mask=public.customers:uuid_value:uuid",
        ], "SELECT email, full_name, address, iban, card_number, uuid_value "
           "FROM customers WHERE id=1",
           "u****@example.com|C*********|*********|DE89**************3000|************1111|"
           "550e8400************************0000"))
        for preset in ("identity", "tc", "phone", "email", "name", "address", "iban", "card", "uuid"):
            self.case(preset + " preset preserves NULL", lambda preset=preset: self.roundtrip([
                "--mask=public.mask_edges:value:" + preset,
            ], "SELECT value IS NULL FROM mask_edges WHERE id=1", "t"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, help="pg_dumpplus executable")
    parser.add_argument("--vanilla", required=True, help="matching upstream pg_dump executable")
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--pg-restore", default="pg_restore")
    parser.add_argument("--maintenance-db", default="postgres")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pgdp_verify_") as work:
        suite = Suite(args, work)
        try:
            suite.setup()
            suite.checks()
        finally:
            suite.cleanup()
        failed = sum(not r["passed"] for r in suite.results)
        print(json.dumps({"passed": len(suite.results) - failed, "failed": failed,
                          "results": suite.results}, ensure_ascii=False))
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
