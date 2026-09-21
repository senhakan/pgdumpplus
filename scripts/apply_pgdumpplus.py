#!/usr/bin/env python3
"""apply_pgdumpplus.py — "pg_dumpplus" (--where=PATTERN:FILTER,
--mask=PATTERN:COLUMN:EXPR) yamasini saf PostgreSQL kaynak agacina
anchor-tabanli uygular. Destek: 13.x, 16.x-18.x.
Kullanim: python3 apply_pgdumpplus.py <src_tree>
Idempotent. Anchor bulunamazsa high-level hata; dosya yazilmaz.
Sonuc: mevcut pg_dump'a DOKUNMAZ; ayni nesnelerden ikinci binary `pg_dumpplus` uretir.
"""
import sys, os, re
import hashlib
import json
import tempfile
from pathlib import Path

PATCH_FILES = (
    "src/include/fe_utils/simple_list.h",
    "src/fe_utils/simple_list.c",
    "src/fe_utils/string_utils.c",
    "src/include/fe_utils/string_utils.h",
    "src/bin/pg_dump/pg_dump.c",
    "src/bin/pg_dump/pg_backup.h",
    "src/bin/pg_dump/pg_backup_archiver.h",
    "src/bin/pg_dump/pg_backup_archiver.c",
    "src/bin/pg_dump/pg_backup_null.c",
    "src/bin/pg_dump/Makefile",
)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic_write(path, data):
    path = Path(path)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=".pgdumpplus-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def rd(p):
    with open(p, encoding="utf-8") as f: return f.read()
class Fail(Exception): pass

def rep_once(s, old, new, label, *extra):
    # Keep source-level string assembly tolerant while extending generated
    # option blocks; an extra adjacent fragment is appended to the replacement.
    if extra:
        new += label.replace("\\\\n", "\n")
        label = extra[0]
    if new in s:                       # idempotent
        return s
    n = s.count(old)
    if n != 1:
        raise Fail(f"anchor [{label}] bulundu={n} (beklenen 1)")
    return s.replace(old, new)

# ---- pg_dump.c icine enjekte edilen C kodu sablonlari (mask ozellikleri) ----
# @@FM@@ -> pg_fatal (PG14+) | fatal (PG13);  @@ARGS@@ -> expand_table... argumanlari
MASK_RESOLVE_C = r"""
	/* pg_dumpplus: --mask=PATTERN:COLUMN:EXPR desenlerini cozumle */
	if (tabledata_mask_patterns.head != NULL)
	{
		SimpleStringListCell *mcell;

		for (mcell = tabledata_mask_patterns.head; mcell; mcell = mcell->next)
		{
			char	   *pat = pg_strdup(mcell->val);
			char	   *c1 = find_unquoted_char(pat, ':');
			char	   *c2;
			char	   *col;
			char	   *expr;
			char	   *literal;
			char	   *query;
			char	   *sql_col;
			PGresult   *colres;
			bool		text_ret = false;
			SimpleStringList one = {NULL, NULL};
			SimpleOidList oids = {NULL, NULL};
			SimpleOidListCell *ocell;

			if (c1 == NULL)
				@@FM@@("missing \":COLUMN:EXPR\" part in --mask pattern \"%s\"",
					   mcell->val);
			*c1 = '\0';
			c2 = find_unquoted_char(c1 + 1, ':');
			if (c2 == NULL)
				@@FM@@("missing \":EXPR\" part in --mask pattern \"%s\"",
					   mcell->val);
			*c2 = '\0';
			col = c1 + 1;
			expr = c2 + 1;

			/* Resolve SQL identifier quoting before catalog lookup. */
			literal = PQescapeLiteral(GetConnection(fout), col, strlen(col));
			if (literal == NULL)
				@@FM@@("could not quote --mask column name");
			query = psprintf("SELECT a[1], pg_catalog.cardinality(a) "
							 "FROM (SELECT pg_catalog.parse_ident(%s) AS a) s", literal);
			PQfreemem(literal);
			colres = ExecuteSqlQuery(fout, query, PGRES_TUPLES_OK);
			if (PQntuples(colres) != 1 || strcmp(PQgetvalue(colres, 0, 1), "1") != 0)
				@@FM@@("--mask column must be a single SQL identifier");
			col = pg_strdup(PQgetvalue(colres, 0, 0));
			PQclear(colres);
			pg_free(query);
			sql_col = pg_strdup(fmtId(col));

			/*
			 * Hazir kaliplar, yaygin kimlik ve iletisim verisi kullanimlari icin
			 * SQL ifadesine donusturulur. "tc" eski adiyla uyum icin korunur.
			 */
			if (strcmp(expr, "identity") == 0 || strcmp(expr, "tc") == 0)
			{
				expr = psprintf("CASE WHEN length(%s::text) <= 4 THEN repeat('*', length(%s::text)) "
								"ELSE left(%s::text, 2) || repeat('*', length(%s::text) - 4) || right(%s::text, 2) END",
								sql_col, sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "phone") == 0)
			{
				expr = psprintf("repeat('*', greatest(length(%s::text) - 3, 0)) || right(%s::text, 3)",
								sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "email") == 0)
			{
				expr = psprintf("CASE WHEN position('@' IN %s::text) > 1 THEN "
								"left(%s::text, 1) || repeat('*', greatest(position('@' IN %s::text) - 2, 0)) || "
								"substring(%s::text FROM position('@' IN %s::text)) "
								"ELSE repeat('*', length(%s::text)) END",
								sql_col, sql_col, sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "name") == 0)
			{
				expr = psprintf("CASE WHEN %s IS NULL THEN NULL WHEN length(trim(%s::text)) > 0 THEN "
								"left(trim(%s::text), 1) || repeat('*', greatest(length(trim(%s::text)) - 1, 0)) "
								"ELSE '' END", sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "address") == 0)
			{
				expr = psprintf("repeat('*', length(%s::text))", sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "iban") == 0)
			{
				expr = psprintf("CASE WHEN length(%s::text) <= 8 THEN repeat('*', length(%s::text)) "
								"ELSE left(%s::text, 4) || repeat('*', length(%s::text) - 8) || right(%s::text, 4) END",
								sql_col, sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "card") == 0 || strcmp(expr, "card_number") == 0)
			{
				expr = psprintf("CASE WHEN length(%s::text) <= 4 THEN repeat('*', length(%s::text)) "
								"ELSE repeat('*', length(%s::text) - 4) || right(%s::text, 4) END",
								sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "uuid") == 0)
			{
				expr = psprintf("CASE WHEN length(%s::text) <= 12 THEN repeat('*', length(%s::text)) "
								"ELSE left(%s::text, 8) || repeat('*', length(%s::text) - 12) || right(%s::text, 4) END",
								sql_col, sql_col, sql_col, sql_col, sql_col);
				text_ret = true;
			}
			else if (strcmp(expr, "all") == 0)
			{
				expr = psprintf("repeat('*', length(%s::text))", sql_col);
				text_ret = true;
			}
			pg_free(sql_col);

			simple_string_list_append(&one, pat);
			expand_table_name_patterns(fout, &one, &oids,
									   @@ARGS@@);
			if (oids.head == NULL)
				@@FM@@("no matching tables were found for --mask pattern \"%s\"",
					   mcell->val);
			for (ocell = oids.head; ocell; ocell = ocell->next)
			{
				DumpMaskEntry *me = pg_malloc(sizeof(DumpMaskEntry));

				me->next = dump_mask_entries;
				me->relid = ocell->val;
				me->colname = pg_strdup(col);
				me->expr = pg_strdup(expr);
				me->text_ret = text_ret;
				me->skip = false;
				dump_mask_entries = me;
			}
		}
	}

"""

# Strict, dependency-free reader for the versioned profile contract.  It is
# deliberately kept in the generated client so installed users do not need
# Python or a JSON library.  The reader converts profile entries into the same
# --where/--mask lists used by the command line and never logs SQL values.
PROFILE_READER_C = r"""
typedef struct PgdpJson
{
	char *p;
	char *end;
	int depth;
} PgdpJson;

static void pgdp_json_ws(PgdpJson *j)
{
	while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' ||
								*j->p == '\r' || *j->p == '\n'))
		j->p++;
}

static void pgdp_json_error(const char *message)
{
	@@FM@@("invalid --profile: %s", message);
}

static bool pgdp_valid_utf8(const unsigned char *s, size_t n)
{
	size_t i = 0;
	while (i < n)
	{
		unsigned char c = s[i++];
		int need;
		if (c < 0x80) continue;
		if (c >= 0xC2 && c <= 0xDF) need = 1;
		else if (c >= 0xE0 && c <= 0xEF) need = 2;
		else if (c >= 0xF0 && c <= 0xF4) need = 3;
		else return false;
		if (i + (size_t) need > n) return false;
		while (need-- > 0)
			if (s[i++] < 0x80 || s[i - 1] > 0xBF) return false;
	}
	return true;
}

static bool pgdp_json_take(PgdpJson *j, char c)
{
	pgdp_json_ws(j);
	if (j->p >= j->end || *j->p != c)
		return false;
	j->p++;
	return true;
}

static char *pgdp_json_string(PgdpJson *j)
{
	char *out = pg_malloc(64);
	size_t length = 0;
	size_t capacity = 64;
	if (!pgdp_json_take(j, '"'))
		pgdp_json_error("expected string");
	while (j->p < j->end)
	{
		unsigned char c = (unsigned char) *j->p++;
		if (c == '"')
		{
			out[length] = '\0';
			return out;
		}
		if (c < 0x20)
			pgdp_json_error("control character in string");
		if (c == '\\')
		{
			if (j->p >= j->end)
				pgdp_json_error("truncated escape");
			c = (unsigned char) *j->p++;
			{
				char value;
				switch (c)
			{
				case '"': value = '"'; break;
				case '\\': value = '\\'; break;
				case '/': value = '/'; break;
				case 'b': value = '\b'; break;
				case 'f': value = '\f'; break;
				case 'n': value = '\n'; break;
				case 'r': value = '\r'; break;
				case 't': value = '\t'; break;
				default: pgdp_json_error("unsupported string escape");
			}
				if (length + 2 > capacity)
				{
					capacity *= 2;
					out = pg_realloc(out, capacity);
				}
				out[length++] = value;
			}
		}
		else
		{
			if (length + 2 > capacity)
			{
				capacity *= 2;
				out = pg_realloc(out, capacity);
			}
			out[length++] = (char) c;
		}
	}
	pgdp_json_error("unterminated string");
	return NULL;
}

static char *pgdp_profile_read(const char *path, size_t *length)
{
	FILE *fp;
	long size;
	char *data;
	if (path == NULL)
		return NULL;
	fp = fopen(path, "rb");
	if (fp == NULL)
		pgdp_json_error("cannot open profile file");
	if (fseek(fp, 0, SEEK_END) != 0 || (size = ftell(fp)) < 0 || size > 262144)
	{
		fclose(fp);
		pgdp_json_error("profile exceeds 256 KiB");
	}
	if (fseek(fp, 0, SEEK_SET) != 0)
		pgdp_json_error("cannot seek profile file");
	data = pg_malloc((size_t) size + 1);
	if (fread(data, 1, (size_t) size, fp) != (size_t) size)
	{
		fclose(fp);
		pg_free(data);
		pgdp_json_error("cannot read profile file");
	}
	fclose(fp);
	data[size] = '\0';
	if (!pgdp_valid_utf8((unsigned char *) data, (size_t) size))
	{
		pg_free(data);
		pgdp_json_error("profile must be UTF-8");
	}
	*length = (size_t) size;
	return data;
}

static void pgdp_profile_string_field(PgdpJson *j, char **target, const char *name)
{
	char *value;
	if (*target != NULL)
		pgdp_json_error("duplicate profile field");
	value = pgdp_json_string(j);
	if (value[0] == '\0')
		pgdp_json_error("profile strings must not be empty");
	*target = value;
}

static void pgdp_profile_filter(PgdpJson *j)
{
	char *table = NULL;
	char *where = NULL;
	bool seen_table = false;
	bool seen_where = false;
	if (++j->depth > 32 || !pgdp_json_take(j, '{'))
		pgdp_json_error("invalid filter object");
	pgdp_json_ws(j);
	if (!pgdp_json_take(j, '}'))
	{
		for (;;)
		{
			char *key = pgdp_json_string(j);
			if (!pgdp_json_take(j, ':'))
				pgdp_json_error("expected ':' in filter");
			if (strcmp(key, "table") == 0)
			{
				if (seen_table) pgdp_json_error("duplicate filter field");
				seen_table = true; pgdp_profile_string_field(j, &table, key);
			}
			else if (strcmp(key, "where") == 0)
			{
				if (seen_where) pgdp_json_error("duplicate filter field");
				seen_where = true; pgdp_profile_string_field(j, &where, key);
			}
			else
				pgdp_json_error("unknown filter field");
			pg_free(key);
			pgdp_json_ws(j);
			if (pgdp_json_take(j, '}')) break;
			if (!pgdp_json_take(j, ',')) pgdp_json_error("expected ',' in filter");
		}
	}
	if (!seen_table || !seen_where)
		pgdp_json_error("filter requires table and where");
	simple_string_list_append(&tabledata_where_patterns, psprintf("%s:%s", table, where));
	pg_free(table); pg_free(where); j->depth--;
}

static void pgdp_profile_mask(PgdpJson *j)
{
	char *table = NULL, *column = NULL, *preset = NULL, *expression = NULL;
	bool seen_table = false, seen_column = false;
	if (++j->depth > 32 || !pgdp_json_take(j, '{'))
		pgdp_json_error("invalid mask object");
	pgdp_json_ws(j);
	if (!pgdp_json_take(j, '}'))
	{
		for (;;)
		{
			char *key = pgdp_json_string(j);
			if (!pgdp_json_take(j, ':')) pgdp_json_error("expected ':' in mask");
			if (strcmp(key, "table") == 0)
			{
				if (seen_table) pgdp_json_error("duplicate mask field");
				seen_table = true; pgdp_profile_string_field(j, &table, key);
			}
			else if (strcmp(key, "column") == 0)
			{
				if (seen_column) pgdp_json_error("duplicate mask field");
				seen_column = true; pgdp_profile_string_field(j, &column, key);
			}
			else if (strcmp(key, "preset") == 0)
				pgdp_profile_string_field(j, &preset, key);
			else if (strcmp(key, "expression") == 0)
				pgdp_profile_string_field(j, &expression, key);
			else
				pgdp_json_error("unknown mask field");
			pg_free(key);
			pgdp_json_ws(j);
			if (pgdp_json_take(j, '}')) break;
			if (!pgdp_json_take(j, ',')) pgdp_json_error("expected ',' in mask");
		}
	}
	if (!seen_table || !seen_column || (preset == NULL) == (expression == NULL))
		pgdp_json_error("mask requires table, column and exactly one of preset/expression");
	simple_string_list_append(&tabledata_mask_patterns,
			psprintf("%s:%s:%s", table, column, preset ? preset : expression));
	pg_free(table); pg_free(column); pg_free(preset); pg_free(expression); j->depth--;
}

static void pgdp_profile_array(PgdpJson *j, bool masks)
{
	if (!pgdp_json_take(j, '[')) pgdp_json_error("expected profile array");
	pgdp_json_ws(j);
	if (!pgdp_json_take(j, ']'))
	{
		for (;;)
		{
			if (masks) pgdp_profile_mask(j); else pgdp_profile_filter(j);
			pgdp_json_ws(j);
			if (pgdp_json_take(j, ']')) break;
			if (!pgdp_json_take(j, ',')) pgdp_json_error("expected ',' in profile array");
		}
	}
}

static void pgdp_load_profile(void)
{
	PgdpJson j;
	char *data, *key;
	size_t length = 0;
	bool version_seen = false, filters_seen = false, masks_seen = false;
	data = pgdp_profile_read(pgdp_profile_path, &length);
	j.p = data; j.end = data + length; j.depth = 0;
	if (!pgdp_json_take(&j, '{')) pgdp_json_error("profile root must be an object");
	pgdp_json_ws(&j);
	if (!pgdp_json_take(&j, '}'))
	{
		for (;;)
		{
			key = pgdp_json_string(&j);
			if (!pgdp_json_take(&j, ':')) pgdp_json_error("expected ':' in profile");
			if (strcmp(key, "schema_version") == 0)
			{
				if (version_seen) pgdp_json_error("duplicate schema_version");
				version_seen = true; pgdp_json_ws(&j);
				if (j.p >= j.end || *j.p != '1') pgdp_json_error("schema_version must be 1");
				j.p++; pgdp_json_ws(&j);
				if (j.p < j.end && *j.p >= '0' && *j.p <= '9') pgdp_json_error("schema_version must be 1");
			}
			else if (strcmp(key, "filters") == 0)
			{
				if (filters_seen) pgdp_json_error("duplicate filters");
				filters_seen = true; pgdp_profile_array(&j, false);
			}
			else if (strcmp(key, "masks") == 0)
			{
				if (masks_seen) pgdp_json_error("duplicate masks");
				masks_seen = true; pgdp_profile_array(&j, true);
			}
			else pgdp_json_error("unknown profile field");
			pg_free(key); pgdp_json_ws(&j);
			if (pgdp_json_take(&j, '}')) break;
			if (!pgdp_json_take(&j, ',')) pgdp_json_error("expected ',' in profile");
		}
	}
	if (!version_seen) pgdp_json_error("schema_version is required");
	pgdp_json_ws(&j);
	if (j.p != j.end) pgdp_json_error("trailing data after profile");
	pg_free(data);
}
"""

BUILD_INFO_C = r'''
	/* pg_dumpplus: build identity is injected by the packaging build. */
#ifndef PGDUMPPLUS_PROJECT_VERSION
#define PGDUMPPLUS_PROJECT_VERSION "unknown"
#endif
#ifndef PGDUMPPLUS_SOURCE_COMMIT
#define PGDUMPPLUS_SOURCE_COMMIT "unknown"
#endif
'''

def build_info_block():
    """Return compile-time identity without adding fragile quoted CPPFLAGS."""
    project = os.environ.get("PGDUMPPLUS_PROJECT_VERSION", "unknown")
    commit = os.environ.get("PGDUMPPLUS_SOURCE_COMMIT", "unknown")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", project):
        project = "unknown"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", commit):
        commit = "unknown"
    return (BUILD_INFO_C
            .replace('#define PGDUMPPLUS_PROJECT_VERSION "unknown"',
                     '#define PGDUMPPLUS_PROJECT_VERSION "' + project + '"')
            .replace('#define PGDUMPPLUS_SOURCE_COMMIT "unknown"',
                     '#define PGDUMPPLUS_SOURCE_COMMIT "' + commit + '"'))

MASK_HELPERS_C = r"""/*
 * pg_dumpplus: Return the masking SQL expression registered for
 * (relid, colname) via --mask, or NULL.  Skipped entries count as NULL.
 */
static char *
mask_expr_for(Oid relid, const char *colname)
{
	DumpMaskEntry *me;

	for (me = dump_mask_entries; me; me = me->next)
	{
		if (!me->skip && me->relid == relid &&
			strcmp(me->colname, colname) == 0)
			return me->expr;
	}
	return NULL;
}

/*
 * pg_dumpplus: Does this table carry any (non-skipped) --mask entry?
 */
static bool
table_has_masks(TableInfo *tbinfo)
{
	DumpMaskEntry *me;

	for (me = dump_mask_entries; me; me = me->next)
		if (!me->skip && me->relid == tbinfo->dobj.catId.oid)
			return true;
	return false;
}

"""

MASK_FMT_C = r"""/*
 * pg_dumpplus: like fmtCopyColumnList, except masked columns are emitted as
 * their SQL masking expression.  Used ONLY for the COPY (SELECT ...)
 * projection; the restore-side COPY header keeps real column names.
 */
static const char *
fmtMaskedColumnList(const TableInfo *ti, PQExpBuffer buffer)
{
	int			numatts = ti->numatts;
	char	  **attnames = ti->attnames;
	bool	   *attisdropped = ti->attisdropped;
	char	   *attgenerated = ti->attgenerated;
	bool		needComma = false;
	int			i;

	appendPQExpBufferChar(buffer, '(');
	for (i = 0; i < numatts; i++)
	{
		char	   *mexpr;

		if (attisdropped[i])
			continue;
		if (attgenerated[i])
			continue;
		if (needComma)
			appendPQExpBufferStr(buffer, ", ");
		mexpr = mask_expr_for(ti->dobj.catId.oid, attnames[i]);
		appendPQExpBufferStr(buffer, mexpr ? mexpr : fmtId(attnames[i]));
		needComma = true;
	}

	if (!needComma)
		return "";						/* no undropped columns */

	appendPQExpBufferChar(buffer, ')');
	return buffer->data;
}

"""

MASK_VALIDATE_C = r"""
	/*
	 * pg_dumpplus: bu tabloya iliskin --mask kayitlarini katalogda dogrula.
	 */
	if (dump_mask_entries != NULL)
	{
		DumpMaskEntry *me;
		int			k;

		for (me = dump_mask_entries; me; me = me->next)
		{
			bool		found = false;

			if (me->skip || me->relid != tbinfo->dobj.catId.oid)
				continue;
			for (k = 0; k < tbinfo->numatts; k++)
			{
				if (strcmp(tbinfo->attnames[k], me->colname) != 0)
					continue;
				found = true;
				if (tbinfo->attisdropped[k])
				{
					@@FM@@("pg_dumpplus: --mask column \"%s\" of \"%s\" is dropped",
						  me->colname, tbinfo->dobj.name);
				}
				else if (tbinfo->attgenerated[k])
				{
					@@FM@@("pg_dumpplus: --mask column \"%s\" of \"%s\" is generated",
						  me->colname, tbinfo->dobj.name);
				}
				else if (me->text_ret &&
						 strcmp(tbinfo->atttypnames[k], "text") != 0 &&
						 strcmp(tbinfo->atttypnames[k], "bpchar") != 0 &&
						 strncmp(tbinfo->atttypnames[k], "character varying", 17) != 0)
					@@FM@@("pg_dumpplus: --mask preset on \"%s\".\"%s\" (%s column) yields text; use a text-compatible column",
						  tbinfo->dobj.name, me->colname, tbinfo->atttypnames[k]);
				break;
			}
			if (!found)
			{
				@@FM@@("pg_dumpplus: --mask column \"%s\" not found in table \"%s\"",
					  me->colname, tbinfo->dobj.name);
			}
		}
	}
"""

MASK_VALIDATE_ALL_C = r"""
/* pg_dumpplus: validate every requested mask before table-data planning. */
static void
validate_all_mask_entries(TableInfo *tblinfo, int numTables)
{
	DumpMaskEntry *me;
	DumpMaskEntry *other;
	int			i;

	for (me = dump_mask_entries; me; me = me->next)
	{
		bool		found_table = false;
		bool		found_column = false;

		for (i = 0; i < numTables; i++)
		{
			TableInfo  *tbinfo = &tblinfo[i];
			int			k;

			if (tbinfo->dobj.catId.oid != me->relid)
				continue;
			found_table = true;
			if (!(tbinfo->dobj.dump & DUMP_COMPONENT_DATA))
				@@FM@@("pg_dumpplus: --mask table \"%s\" is not selected for data export",
					  tbinfo->dobj.name);
			for (k = 0; k < tbinfo->numatts; k++)
			{
				if (strcmp(tbinfo->attnames[k], me->colname) != 0)
					continue;
				found_column = true;
				if (tbinfo->attisdropped[k])
					@@FM@@("pg_dumpplus: --mask column \"%s\" of \"%s\" is dropped",
						  me->colname, tbinfo->dobj.name);
				if (tbinfo->attgenerated[k])
					@@FM@@("pg_dumpplus: --mask column \"%s\" of \"%s\" is generated",
						  me->colname, tbinfo->dobj.name);
				if (me->text_ret &&
					strcmp(tbinfo->atttypnames[k], "text") != 0 &&
					strcmp(tbinfo->atttypnames[k], "bpchar") != 0 &&
					strncmp(tbinfo->atttypnames[k], "character varying", 17) != 0)
					@@FM@@("pg_dumpplus: --mask preset on \"%s\".\"%s\" (%s column) yields text; use a text-compatible column",
						  tbinfo->dobj.name, me->colname, tbinfo->atttypnames[k]);
				break;
			}
			break;
		}
		if (!found_table)
			@@FM@@("pg_dumpplus: --mask target table was not found");
		if (!found_column)
			@@FM@@("pg_dumpplus: --mask column \"%s\" was not found in the target table",
				  me->colname);
	}

	for (me = dump_mask_entries; me; me = me->next)
		for (other = me->next; other; other = other->next)
			if (me->relid == other->relid && strcmp(me->colname, other->colname) == 0)
				@@FM@@("pg_dumpplus: duplicate --mask rule for column \"%s\"",
					  me->colname);
}

"""

MASK_DRYRUN_C = r"""
static void
pgdp_json_quoted(const char *value)
{
	const unsigned char *p;
	putchar('"');
	for (p = (const unsigned char *) value; *p; p++)
	{
		if (*p == '"' || *p == '\\')
			printf("\\%c", *p);
		else if (*p < 32)
			printf("\\u%04x", *p);
		else
			putchar(*p);
	}
	putchar('"');
}

static void
pgdp_emit_plan(TableInfo *tblinfo, int numTables)
{
	DumpMaskEntry *me;
	int i;
	bool first = true;

	if (strcmp(pgdp_plan_format, "json") == 0)
		printf("{\"schema_version\":1,\"masks\":[");
	else
		printf("pg_dumpplus dry-run plan\n");
	for (me = dump_mask_entries; me; me = me->next)
	{
		for (i = 0; i < numTables; i++)
		{
			TableInfo *tbinfo = &tblinfo[i];
			int k;
			if (tbinfo->dobj.catId.oid != me->relid)
				continue;
			for (k = 0; k < tbinfo->numatts; k++)
			{
				if (strcmp(tbinfo->attnames[k], me->colname) != 0)
					continue;
				if (strcmp(pgdp_plan_format, "json") == 0)
				{
					if (!first) printf(",");
					printf("{\"table\":"); pgdp_json_quoted(tbinfo->dobj.name);
					printf(",\"column\":"); pgdp_json_quoted(me->colname);
					printf(",\"type\":"); pgdp_json_quoted(tbinfo->atttypnames[k]);
					printf(",\"mask\":"); pgdp_json_quoted(me->text_ret ? "preset" : "custom");
					printf(",\"filter\":%s}",
						   simple_oid_list_member(&tabledata_where_oids, tbinfo->dobj.catId.oid) ? "true" : "false");
				}
				else
					printf("table=%s column=%s type=%s mask=%s filter=%s\n",
						   tbinfo->dobj.name, me->colname, tbinfo->atttypnames[k],
						   me->text_ret ? "preset" : "custom",
						   simple_oid_list_member(&tabledata_where_oids, tbinfo->dobj.catId.oid) ? "true" : "false");
				first = false;
			}
		}
	}
	if (strcmp(pgdp_plan_format, "json") == 0)
		printf("]}\n");
}
"""

def main(root):
    P = lambda *a: os.path.join(root, *a)
    manifest = Path(root) / ".pgdumpplus-patch.json"
    patcher_hash = digest(Path(__file__).read_bytes())
    originals = {P(name): Path(P(name)).read_bytes() for name in PATCH_FILES}
    if manifest.exists():
        state = json.loads(manifest.read_text())
        expected = {name: digest(originals[P(name)]) for name in PATCH_FILES}
        if state.get("patcher") != patcher_hash or state.get("files") != expected:
            raise Fail("patch revision or source files changed; use a clean upstream source tree")
        print("Already applied; all patched file hashes verified.")
        return
    if any(b"pg_dumpplus" in data for data in originals.values()):
        raise Fail("older or incomplete patch detected; use a clean upstream source tree")

    # Validate every anchor in memory before changing any source file.
    pending = {}
    def wr(path, content):
        pending[path] = content.encode("utf-8")

    changed = []

    # ---------- 1) fe_utils/simple_list.h ----------
    h = P("src","include","fe_utils","simple_list.h"); t = rd(h)
    t = rep_once(t,
        "\tstruct SimpleOidListCell *next;\n\tOid\t\t\tval;\n} SimpleOidListCell;",
        "\tstruct SimpleOidListCell *next;\n\tOid\t\t\tval;\n\tvoid\t   *extra_data;\t\t/* pg_dumpplus: optional payload, or NULL */\n} SimpleOidListCell;",
        "slh-struct")
    t = rep_once(t,
        "extern void simple_oid_list_append(SimpleOidList *list, Oid val);\nextern bool simple_oid_list_member(SimpleOidList *list, Oid val);",
        "extern void simple_oid_list_append(SimpleOidList *list, Oid val);\nextern void simple_oid_list_append_data(SimpleOidList *list, Oid val,\n\t\t\t\t\t\t\t\t\t\t\t\t\tvoid *extra_data);\nextern bool simple_oid_list_member(SimpleOidList *list, Oid val);\nextern bool simple_oid_list_find_data(SimpleOidList *list, Oid val,\n\t\t\t\t\t\t\t\t\t\t\t\t\t  void **extra_data);",
        "slh-protos")
    wr(h, t); changed.append(h)

    # ---------- 2) fe_utils/simple_list.c ----------
    c = P("src","fe_utils","simple_list.c"); t = rd(c)
    t = rep_once(t,
"""void
simple_oid_list_append(SimpleOidList *list, Oid val)
{
\tSimpleOidListCell *cell;

\tcell = (SimpleOidListCell *) pg_malloc(sizeof(SimpleOidListCell));
\tcell->next = NULL;
\tcell->val = val;

\tif (list->tail)
\t\tlist->tail->next = cell;
\telse
\t\tlist->head = cell;
\tlist->tail = cell;
}

/*
 * Is OID present in the list?
 */
bool
simple_oid_list_member(SimpleOidList *list, Oid val)
{
\tSimpleOidListCell *cell;

\tfor (cell = list->head; cell; cell = cell->next)
\t{
\t\tif (cell->val == val)
\t\t\treturn true;
\t}
\treturn false;
}""",
"""void
simple_oid_list_append(SimpleOidList *list, Oid val)
{
\tsimple_oid_list_append_data(list, val, NULL);
}

/*
 * pg_dumpplus: Append an OID to the list, along with extra pointer-sized data.
 */
void
simple_oid_list_append_data(SimpleOidList *list, Oid val, void *extra_data)
{
\tSimpleOidListCell *cell;

\tcell = (SimpleOidListCell *) pg_malloc(sizeof(SimpleOidListCell));
\tcell->next = NULL;
\tcell->val = val;
\tcell->extra_data = extra_data;

\tif (list->tail)
\t\tlist->tail->next = cell;
\telse
\t\tlist->head = cell;
\tlist->tail = cell;
}

/*
 * Is OID present in the list?
 */
bool
simple_oid_list_member(SimpleOidList *list, Oid val)
{
\treturn simple_oid_list_find_data(list, val, NULL);
}

/*
 * pg_dumpplus: Is OID present?  If so and extra_data != NULL, store the
 * cell's associated extra data through *extra_data.
 */
bool
simple_oid_list_find_data(SimpleOidList *list, Oid val, void **extra_data)
{
\tSimpleOidListCell *cell;

\tfor (cell = list->head; cell; cell = cell->next)
\t{
\t\tif (cell->val == val)
\t\t{
\t\t\tif (extra_data)
\t\t\t\t*extra_data = cell->extra_data;
\t\t\treturn true;
\t\t}
\t}
\treturn false;
}""", "slc")
    wr(c, t); changed.append(c)

    # ---------- 3) fe_utils/string_utils.[ch] ----------
    su = P("src","fe_utils","string_utils.c"); t = rd(su)
    if "find_unquoted_char" not in t:
        t = t.rstrip("\n") + """

/*
 * pg_dumpplus: Find the first occurrence of 'sep' in 's' that is not inside a
 * double-quoted SQL identifier ("" is an escaped quote).  Returns a pointer
 * into s, or NULL.
 */
char *
find_unquoted_char(const char *s, char sep)
{
\tbool\t\tin_quotes = false;

\twhile (*s)
\t{
\t\tif (*s == '\"')
\t\t{
\t\t\tif (in_quotes && s[1] == '\"')
\t\t\t\ts++;
\t\t\telse
\t\t\t\tin_quotes = !in_quotes;
\t\t}
\t\telse if (*s == sep && !in_quotes)
\t\t\treturn (char *) s;
\t\ts++;
\t}
\treturn NULL;
}
"""
        wr(su, t); changed.append(su)
    sh = P("src","include","fe_utils","string_utils.h"); t = rd(sh)
    t = rep_once(t, "#endif\t\t\t\t\t\t\t/* STRING_UTILS_H */",
        "extern char *find_unquoted_char(const char *s, char sep);\t/* pg_dumpplus */\n\n#endif\t\t\t\t\t\t\t/* STRING_UTILS_H */",
        "suh")
    wr(sh, t); changed.append(sh)

    # ---------- 4) pg_dump.c ----------
    d = P("src","bin","pg_dump","pg_dump.c"); t = rd(d)
    PG13 = "pg_fatal(" not in t      # PG13'te pg_fatal yok -> fatal()
    if "pg_dumpplus" not in t:
        # 4a: statik listeler
        t = build_info_block() + t
        t = rep_once(t, "static SimpleOidList tabledata_exclude_oids = {NULL, NULL};",
            "static SimpleOidList tabledata_exclude_oids = {NULL, NULL};\n"
            "/* pg_dumpplus: --where desenleri ve cozumlenmis OID'leri */\n"
            "static SimpleStringList tabledata_where_patterns = {NULL, NULL};\n"
            "static SimpleOidList tabledata_where_oids = {NULL, NULL};",
            "dd-statics")
        # 4b: long_options (opsiyon kodu 26; 13/17/18'de bos)
        t = rep_once(t, '{"include-foreign-data", required_argument, NULL, 11},',
            '{"include-foreign-data", required_argument, NULL, 11},\n\t\t{"where", required_argument, NULL, 26},\t/* pg_dumpplus */',
            "dd-longopt")
        # 4c: case 26 — case 11 blogundan hemen sonra
        m11 = re.search(r"case 11:[^\0]*?break;\n", t)
        if not m11: raise Fail("anchor [dd-case11] yok")
        t = t[:m11.end()] + """
\t\t\tcase 26:\t\t\t\t/* pg_dumpplus: --where=PATTERN:FILTER */
\t\t\t\tsimple_string_list_append(&tabledata_where_patterns, optarg);
\t\t\t\tbreak;

""" + t[m11.end():]
        # 4d: version banner -> progname bazli. Dikkat: [pg_dumpplus] etiketi
        # YALNIZ progname==pg_dumpplus iken basilir; ayni dizindeki pg_dumpall,
        # pg_dump --version ciktilarinin "pg_dump (PostgreSQL) X.Y" regex'ine
        # uymasini bekler (ayni-version kontrolu) — aksi halde TAP 001/002 cakisir.
        v1 = 'puts("pg_dump (PostgreSQL) " PG_VERSION);'
        v2 = 'printf("pg_dump (PostgreSQL) " PG_VERSION "\\n");'
        vnew = ('if (strcmp(progname, "pg_dump") == 0)\n'
                '\t\t\t\tprintf("%s (PostgreSQL) %s\\n", progname, PG_VERSION);\n'
                '\t\t\telse\n'
                '\t\t\t\tprintf("%s (PostgreSQL) %s [pg_dumpplus]\\n", progname, PG_VERSION);')
        if v1 in t:   t = rep_once(t, v1, vnew, "dd-ver-puts")
        elif v2 in t: t = rep_once(t, v2, vnew, "dd-ver-printf")
        else: raise Fail("dd-version anchor yok")
        # 4e: help
        t = rep_once(t, 'printf(_("\\nConnection options:\\n"));',
            'printf(_("  --where=PATTERN:FILTER   dump only rows matching SQL FILTER for\\n"\n'
            '\t\t\t\t\t "                               tables matching PATTERN (pg_dumpplus)\\n"));\n'
            '\tprintf(_("\\nConnection options:\\n"));', "dd-help")
        # 4f: forward decl
        mfwd = re.search(r"static void expand_table_name_patterns\(Archive \*fout,\n[^\0]*?\);\n", t)
        if not mfwd: raise Fail("anchor [dd-fwd] yok")
        fwd = mfwd.group(0)
        fwd_new = fwd[:-len(");\n")] + ", bool with_extra_data);\n"
        t = t[:mfwd.start()] + fwd_new + t[mfwd.end():]
        # 4g: tanim imzasi
        mdef = re.search(r"static void\nexpand_table_name_patterns\(Archive \*fout,\n"
                         r"[ \t]+SimpleStringList \*patterns, SimpleOidList \*oids,\n"
                         r"[ \t]+bool strict_names(, bool with_child_tables)?\)\n", t)
        if not mdef: raise Fail("anchor [dd-def] yok")
        has_children = mdef.group(1) is not None
        t = t[:mdef.start()] + mdef.group(0).replace(")\n", ", bool with_extra_data)\n") + t[mdef.end():]
        # 4g2: govde duzenlemeleri (yalniz bu fonksiyon diliminde)
        dstart = mdef.end()
        dend = t.find("\n\tdestroyPQExpBuffer(query);\n}\n", dstart)
        if dend < 0: raise Fail("dd-govde sonu yok")
        body = t[dstart:dend]
        body = rep_once(body,
            "\tPQExpBuffer query;\n\tPGresult   *res;\n\tSimpleStringListCell *cell;\n\tint\t\t\ti;\n",
            "\tPQExpBuffer query;\n\tPGresult   *res;\n\tSimpleStringListCell *cell;\n\tchar\t   *extra_data;\t/* pg_dumpplus */\n\tint\t\t\ti;\n",
            "dd-locals")
        colon_err = ("\t\t\t\tfatal(\"missing \\\":FILTER\\\" part in --where pattern \\\"%s\\\"\",\n"
                     "\t\t\t\t\t\tcell->val);\n") if PG13 else \
                    ("\t\t\t\tpg_fatal(\"missing \\\":FILTER\\\" part in --where pattern \\\"%s\\\"\",\n"
                     "\t\t\t\t\t\t cell->val);\n")
        body = rep_once(body, "\tfor (cell = patterns->head; cell; cell = cell->next)\n\t{\n",
            "\tfor (cell = patterns->head; cell; cell = cell->next)\n\t{\n"
            "\t\t/*\n\t\t * pg_dumpplus: with_extra_data ise desen 'TABLO:FILTRE' bicimindedir;\n"
            "\t\t * tirlimak icinde olmayan ilk ':' ayiracindan bol, sag tarafi extra_data olur.\n"
            "\t\t */\n"
            "\t\textra_data = NULL;\n"
            "\t\tif (with_extra_data)\n"
            "\t\t{\n"
            "\t\t\tchar\t   *colon = find_unquoted_char(cell->val, ':');\n\n"
            "\t\t\tif (colon == NULL)\n"
            + colon_err +
            "\t\t\t*colon = '\\0';\n"
            "\t\t\textra_data = pg_strdup(colon + 1);\n"
            "\t\t}\n\n", "dd-split")
        body = rep_once(body,
            "simple_oid_list_append(oids, atooid(PQgetvalue(res, i, 0)));",
            "simple_oid_list_append_data(oids,\n\t\t\t\t\t\t\t\t\t\t\t\t\t  atooid(PQgetvalue(res, i, 0)),\n\t\t\t\t\t\t\t\t\t\t\t\t\t  extra_data);",
            "dd-append")
        t = t[:dstart] + body + t[dend:]
        # 4h: mevcut cagri noktalarina 'false'
        if has_children:
            t, ncalls = re.subn(r"expand_table_name_patterns\(fout,\s+(&\w+),\s+(&\w+),\s+(strict_names|false),\s+(true|false)\);",
                                r"expand_table_name_patterns(fout, \1,\n\t\t\t\t\t\t\t   \2,\n\t\t\t\t\t\t\t   \3, \4, false);", t)
        else:
            t, ncalls = re.subn(r"expand_table_name_patterns\(fout,\s+(&\w+),\s+(&\w+),\s+(strict_names|false)\);",
                                r"expand_table_name_patterns(fout, \1,\n\t\t\t\t\t\t\t   \2,\n\t\t\t\t\t\t\t   \3, false);", t)
        if ncalls < 2: raise Fail(f"dd-cagri noktalan yetersiz ({ncalls})")
        # 4i: --where genisletme cagrisi — son genisletme sonrasina
        where_args = "true, false, true" if has_children else "true, true"
        mask_args = "true, false, false" if has_children else "true, false"
        last = max(x.end() for x in re.finditer(r"expand_table_name_patterns\(fout[^\0]*?\);\n", t))
        no_match_err = 'fatal("no matching tables were found for --where pattern");' if PG13 \
                       else 'pg_fatal("no matching tables were found for --where pattern");'
        t = t[:last] + ("\n\t/* pg_dumpplus: --where=PATTERN:FILTER desenlerini OID'lere coz */\n"
                        "\tif (tabledata_where_patterns.head != NULL)\n"
                        "\t{\n"
                        "\t\texpand_table_name_patterns(fout, &tabledata_where_patterns,\n"
                        "\t\t\t\t\t\t\t\t\t   &tabledata_where_oids,\n"
                        f"\t\t\t\t\t\t\t\t\t   {where_args});\n"
                        "\t\tif (tabledata_where_oids.head == NULL)\n"
                        f"\t\t\t{no_match_err}\n"
                        "\t}\n\n") + t[last:]
        fm_err = "fatal" if PG13 else "pg_fatal"
        t = rep_once(t, "\ttblinfo = getSchemaData(fout, &numTables);",
                     "\ttblinfo = getSchemaData(fout, &numTables);\n"
                     f"\tif (pgdp_plan_format_set && !pgdp_dry_run)\n\t\t{fm_err}(\"--plan-format requires --dry-run\");\n"
                     f"\tif (pgdp_dry_run && filename != NULL)\n\t\t{fm_err}(\"--dry-run cannot be used with --file\");\n"
                     "\t/* PostgreSQL 18 keeps --schema-only in a local option; older majors store it in DumpOptions. */\n"
                     "#if PG_VERSION_NUM >= 180000\n"
                     f"\tif (schema_only && dump_mask_entries != NULL)\n\t\t{fm_err}(\"--mask cannot be used with --schema-only\");\n"
                     "#else\n"
                     f"\tif (dopt.schemaOnly && dump_mask_entries != NULL)\n\t\t{fm_err}(\"--mask cannot be used with --schema-only\");\n"
                     "#endif\n"
                     "\tif (dump_mask_entries != NULL)\n"
                     "\t\tvalidate_all_mask_entries(tblinfo, numTables);\n"
                     "\tif (pgdp_dry_run)\n"
                     "\t{\n"
                     "\t\tif (pgdp_plan_format_set && strcmp(pgdp_plan_format, \"text\") != 0 && strcmp(pgdp_plan_format, \"json\") != 0)\n"
                     f"\t\t\t{fm_err}(\"--plan-format must be text or json\");\n"
                     "\t\tpgdp_emit_plan(tblinfo, numTables);\n"
                     "\t\texit_nicely(0);\n"
                     "\t}",
                     "dd-mask-validate-all")
        # 4j: makeTableDataInfo
        mmt = re.search(r"static void\nmakeTableDataInfo\(DumpOptions \*dopt, TableInfo \*tbinfo\)\n\{\n\tTableDataInfo \*tdinfo;\n", t)
        if not mmt: raise Fail("anchor [dd-mtdi-head] yok")
        t = t[:mmt.end()] + "\tchar\t   *filter_clause;\t\t/* pg_dumpplus */\n" + t[mmt.end():]
        t = rep_once(t, "\ttdinfo->filtercond = NULL;\t/* might get set later */",
            "\ttdinfo->filtercond = NULL;\t/* might get set later */\n\n"
            "\t/*\n\t * pg_dumpplus: --where=PATTERN:FILTER bu tabloya eslesiyorsa filtercond'u kur.\n"
            "\t * Ornegin filter_clause \"created_at >= '2026-09-15'\" ise dump sorgusuna\n"
            "\t * \"WHERE (created_at >= '2026-09-15')\" eklenir (COPY ya da INSERT yolu).\n"
            "\t */\n"
            "\tfilter_clause = NULL;\n"
            "\tif (simple_oid_list_find_data(&tabledata_where_oids,\n"
            "\t\t\t\t\t\t\t\t\t\t\t\t\ttbinfo->dobj.catId.oid,\n"
            "\t\t\t\t\t\t\t\t\t\t\t\t\t(void **) &filter_clause) && filter_clause)\n"
            "\t{\n"
            "\t\tif (tdinfo->dobj.objType != DO_TABLE_DATA)\n"
            "\t\t\tpg_log_warning(\"pg_dumpplus: --where filter for \\\"%s\\\" ignored: object is not plain table data\",\n"
            "\t\t\t\t\t  tbinfo->dobj.name);\n"
            "\t\telse\n"
            "\t\t\ttdinfo->filtercond = psprintf(\"WHERE (%s)\", filter_clause);\n"
            "\t}", "dd-mtdi-filter")
        wr(d, t); changed.append(d)

    # ---------- 5) pg_dump.c: --mask (KVKK/GDPR kolon maskeleme) ----------
    if "{\"mask\", required_argument" not in t:
        # 5a: statikler + kayit tipi
        t = rep_once(t, "static SimpleOidList tabledata_where_oids = {NULL, NULL};",
            "static SimpleOidList tabledata_where_oids = {NULL, NULL};\n"
            "/* pg_dumpplus: --mask desenleri ve cozumlenmis kayitlari */\n"
            "static SimpleStringList tabledata_mask_patterns = {NULL, NULL};\n"
            "typedef struct DumpMaskEntry\n"
            "{\n"
            "\tstruct DumpMaskEntry *next;\n"
            "\tOid\t\t\t\t\trelid;\n"
            "\tchar\t   *colname;\n"
            "\tchar\t   *expr;\n"
            "\tbool\t\t\ttext_ret;\t\t/* preset text uretiyor */\n"
            "\tbool\t\t\tskip;\t\t\t/* validation'da true olur */\n"
            "} DumpMaskEntry;\n"
            "static DumpMaskEntry *dump_mask_entries = NULL;\n"
            "static bool pgdp_dry_run = false;\n"
            "static bool pgdp_stats = false;\n"
            "static const char *pgdp_plan_format = \"text\";\n"
            "static bool pgdp_plan_format_set = false;\n"
            "static const char *pgdp_profile_path = NULL;\n"
            "static void pgdp_load_profile(void);\n"
            "/* pg_dumpplus: forward decls (tanimlar fmtCopyColumnList oncesi) */\n"
            "static char *mask_expr_for(Oid relid, const char *colname);\n"
            "static bool table_has_masks(TableInfo *tbinfo);\n"
            "static void pgdp_emit_plan(TableInfo *tblinfo, int numTables);\n"
            "static const char *fmtMaskedColumnList(const TableInfo *ti,\n"
			"\t\t\t\t\t\t\t\t\t\t\tPQExpBuffer buffer);\n"
			"static void validate_all_mask_entries(TableInfo *tblinfo, int numTables);",
            "dm-statics")
        # 5b: long_options 27 (26=where'den sonra)
        t = rep_once(t, '{"where", required_argument, NULL, 26},\t/* pg_dumpplus */',
            '{"where", required_argument, NULL, 26},\t/* pg_dumpplus */\n'
            '\t\t{"mask", required_argument, NULL, 27},\t\t/* pg_dumpplus */\n'
            '\t\t{"dry-run", no_argument, NULL, 28},\t\t/* pg_dumpplus */\n'
            '\t\t{"plan-format", required_argument, NULL, 29},\t/* pg_dumpplus */\n'
            '\t\t{"build-info", no_argument, NULL, 30},\t\t/* pg_dumpplus */\n'
            '\t\t{"profile", required_argument, NULL, 31},\t\t/* pg_dumpplus */\n'
            '\t\t{"stats", no_argument, NULL, 32},\t\t/* pg_dumpplus */',
            "dm-longopt")
        # 5c: case 27 (case 26 blogundan hemen sonra)
        t = rep_once(t, "\t\t\tcase 26:\t\t\t\t/* pg_dumpplus: --where=PATTERN:FILTER */\n"
                        "\t\t\t\tsimple_string_list_append(&tabledata_where_patterns, optarg);\n"
                        "\t\t\t\tbreak;\n",
            "\t\t\tcase 26:\t\t\t\t/* pg_dumpplus: --where=PATTERN:FILTER */\n"
            "\t\t\t\tsimple_string_list_append(&tabledata_where_patterns, optarg);\n"
            "\t\t\t\tbreak;\n"
            "\t\t\tcase 27:\t\t\t\t/* pg_dumpplus: --mask=PATTERN:COLUMN:EXPR */\n"
            "\t\t\t\tsimple_string_list_append(&tabledata_mask_patterns, optarg);\n"
            "\t\t\t\tbreak;\n"
            '\t\t\tcase 28:\t\t\t\tpgdp_dry_run = true; break;\n'
            '\t\t\tcase 29:\t\t\t\tpgdp_plan_format = pg_strdup(optarg); pgdp_plan_format_set = true; break;\n'
            '\t\t\tcase 30:\t\t\t\tprintf("pg_dumpplus project %s; PostgreSQL %s; source %s\\n", PGDUMPPLUS_PROJECT_VERSION, PG_VERSION, PGDUMPPLUS_SOURCE_COMMIT); exit(0);\n',
            '\t\t\tcase 31:\t\t\t\tpgdp_profile_path = pg_strdup(optarg); break;\\n'
            '\t\t\tcase 32:\t\t\t\tpgdp_stats = true;\n'
            '#if PG_VERSION_NUM >= 140000\n'
            '\t\t\t\tpg_logging_increase_verbosity();\n'
            '#else\n'
            '\t\t\t\tpg_logging_set_level(PG_LOG_INFO);\n'
            '#endif\n'
            '\t\t\t\tbreak;\\n',
            "dm-case")
        t = t.replace(r"pgdp_profile_path = pg_strdup(optarg); break;\n",
                      "pgdp_profile_path = pg_strdup(optarg); break;\n")
        # The profile case historically used a literal ``\\n`` sentinel in
        # the replacement text.  Normalize the newly added stats case too,
        # otherwise PG13 sees the two characters ``\\n`` in generated C.
        t = t.replace("\t\t\t\tbreak;\\n", "\t\t\t\tbreak;\n")
        t = t.replace(r"pgdp_stats = true; pg_logging_increase_verbosity(); break;\n",
                      "pgdp_stats = true;\n"
                      "#if PG_VERSION_NUM >= 140000\n"
                      "pg_logging_increase_verbosity();\n"
                      "#else\n"
                      "pg_logging_set_level(PG_LOG_INFO);\n"
                      "#endif\n"
                      "break;\n")
        # 5d: help — --where satirindan once
        where_help = 'printf(_("  --where=PATTERN:FILTER   dump only rows matching SQL FILTER for\\n"'
        t = rep_once(t, where_help,
            'printf(_("  --mask=PATTERN:COLUMN:EXPR   replace COLUMN value with EXPR in dumped\\n"'
            '\t\t\t\t\t "                               data; EXPR: SQL or preset identity|phone|email|name|address|iban|card|uuid|all\\n"));\n'
            'printf(_("  --dry-run                    print a catalog-only export plan\\n"));\n'
            'printf(_("  --plan-format=text|json      select dry-run plan format\\n"));\n'
            'printf(_("  --build-info                 print project, upstream and source identity\\n"));\n'
            'printf(_("  --profile=FILE               load a strict JSON profile (schema_version 1)\\n"));\n'
            'printf(_("  --stats                      show rows and exported bytes after each table\\n"));\n'
            + where_help, "dm-help")
        # 5e: cozum blogu — --where cozum bloğunun ardina
        mw = re.search(r"if \(tabledata_where_oids\.head == NULL\)\n[ \t]*\S[^\0]*?\n[ \t]*\}\n", t)
        if not mw: raise Fail("anchor [dm-after-where] yok")
        fm_err = "fatal" if PG13 else "pg_fatal"
        block = (MASK_RESOLVE_C.replace("@@FM@@", fm_err)
                            .replace("@@ARGS@@", mask_args))
        t = t[:mw.end()] + block + t[mw.end():]
        # Profiles are parsed by the compiled client after getopt has handled
        # all CLI options, so both inputs share the same rule lists and merge
        # semantics.  Loading happens before any database connection.
        profile_anchor = "\n\t/* --column-inserts implies --inserts */"
        profile_insert = ("\n\tif (pgdp_profile_path != NULL)\n"
                          "\t\tpgdp_load_profile();\n")
        t = rep_once(t, profile_anchor, profile_insert + profile_anchor, "dm-profile-load")
        wr(d, t); changed.append(d)

    # ---------- 6) pg_dump.c: maskeleme yardimcilari ve sorgu yollari ----------
    if "static const char *\nfmtMaskedColumnList" not in t:
        # 6a: yardimcilar fmtCopyColumnList tanimindan once
        mfc = re.search(r"static const char \*\nfmtCopyColumnList\(const TableInfo \*ti, PQExpBuffer buffer\)\n\{\n", t)
        if not mfc: raise Fail("anchor [dm-fmt] yok")
        pos = mfc.start()
        t = t[:pos] + PROFILE_READER_C.replace("@@FM@@", fm_err) + MASK_HELPERS_C + MASK_VALIDATE_ALL_C.replace("@@FM@@", fm_err) + MASK_DRYRUN_C.replace("@@FM@@", fm_err) + MASK_FMT_C + t[pos:]
        # 6b: COPY (SELECT) provizyonu -> maskeli liste; restore header'a dokunulmaz
        t = rep_once(t,
            "column_list = fmtCopyColumnList(tbinfo, clistBuf);",
            "column_list = table_has_masks(tbinfo)\n\t\t\t\t\t? fmtMaskedColumnList(tbinfo, clistBuf)\n\t\t\t\t\t: fmtCopyColumnList(tbinfo, clistBuf);",
            "dm-colproj")
        # 6c: COPY (SELECT ...) TO dalini mask icin de tetikle
        t = rep_once(t,
            "if (tdinfo->filtercond || tbinfo->relkind == RELKIND_FOREIGN_TABLE)",
            "if (tdinfo->filtercond || table_has_masks(tbinfo) ||\n\t\ttbinfo->relkind == RELKIND_FOREIGN_TABLE)",
            "dm-copybranch")
        # 6d: --inserts cursor SELECT'inde maskeli kolon -> ifade
        t = rep_once(t,
            "\t\tif (tbinfo->attgenerated[i])\n\t\t\tappendPQExpBufferStr(q, \"NULL\");\n\t\telse\n\t\t\tappendPQExpBufferStr(q, fmtId(tbinfo->attnames[i]));",
            "\t\tif (tbinfo->attgenerated[i])\n\t\t\tappendPQExpBufferStr(q, \"NULL\");\n"
            "\t\telse\n\t\t{\n\t\t\t/* pg_dumpplus: masked column -> emit masking expression */\n"
            "\t\t\tchar\t   *mexpr = mask_expr_for(tbinfo->dobj.catId.oid,\n\t\t\t\t\t\t\t\t\t\ttbinfo->attnames[i]);\n\n"
            "\t\t\tif (mexpr)\n"
            "\t\t\t\tappendPQExpBuffer(q, \"%s AS %s\", mexpr, fmtId(tbinfo->attnames[i]));\n"
            "\t\t\telse\n"
            "\t\t\t\tappendPQExpBufferStr(q, fmtId(tbinfo->attnames[i]));\n\t\t}",
            "dm-inserts")
        # 6e: --stats COPY akisi. Satir sayisi COPY'nin kendi COMMAND_OK
        # sonucundan, byte sayisi WriteData'a giden buffer uzunluklarindan gelir.
        # Ek COUNT/EXPLAIN veya payload taramasi yapilmaz.
        t = rep_once(t,
            "\tint\t\t\tret;\n\tchar\t   *copybuf;",
            "\tint\t\t\tret;\n"
            "\tuint64\t\tpgdp_stats_bytes = 0;\t/* pg_dumpplus --stats */\n"
            "\tchar\t\t*copybuf;",
            "ds-copy-locals")
        t = rep_once(t,
            "\tPGresult   *res;\n\tint\t\t\tret;\n"
            "\tuint64\t\tpgdp_stats_bytes = 0;\t/* pg_dumpplus --stats */\n"
            "\tchar\t\t*copybuf;",
            "\tPGresult   *res;\n\tint\t\t\tret;\n"
            "\tuint64\t\tpgdp_stats_bytes = 0;\t/* pg_dumpplus --stats */\n"
            "\tchar\t\t*copybuf;\n"
            "\n\tif (pgdp_stats)\n"
            "\t{\n"
            "\t\tfout->pgdp_stats_active = true;\n"
            "\t\tfout->pgdp_stats_rows_valid = false;\n"
            "\t\tfout->pgdp_stats_rows = 0;\n"
            "\t\tfout->pgdp_stats_bytes = 0;\n"
            "\t}",
            "ds-copy-state")
        t = rep_once(t,
            "\t\tif (copybuf)\n\t\t{\n\t\t\tWriteData(fout, copybuf, ret);",
            "\t\tif (copybuf)\n\t\t{\n\t\t\tWriteData(fout, copybuf, ret);\n"
            "\t\t\tif (pgdp_stats)\n"
            "\t\t\t\tpgdp_stats_bytes += (uint64) ret;",
            "ds-copy-bytes")
        stats_error = "fatal" if PG13 else "pg_fatal"
        t = rep_once(t,
            "\tPQclear(res);\n\n\t/* Do this to ensure we've pumped libpq back to idle state */",
            "\tif (pgdp_stats)\n"
            "\t{\n"
            "\t\tconst char *pgdp_stats_rows = PQcmdTuples(res);\n"
            "\t\tchar *pgdp_stats_end = NULL;\n\n"
            f"\t\tif (pgdp_stats_rows == NULL || pgdp_stats_rows[0] == '\\0')\n"
            f"\t\t\t{stats_error}(\"--stats could not read COPY row count for table \\\"%s\\\"\", classname);\n"
            "\t\terrno = 0;\n"
            "\t\tfout->pgdp_stats_rows = (uint64) strtoull(pgdp_stats_rows, &pgdp_stats_end, 10);\n"
            f"\t\tif (errno == ERANGE || pgdp_stats_end == pgdp_stats_rows || *pgdp_stats_end != '\\0')\n"
            f"\t\t\t{stats_error}(\"--stats received invalid COPY row count for table \\\"%s\\\"\", classname);\n"
            "\t\tfout->pgdp_stats_bytes = pgdp_stats_bytes;\n"
            "\t\tfout->pgdp_stats_rows_valid = true;\n"
            "\t}\n"
            "\tPQclear(res);\n\n\t/* Do this to ensure we've pumped libpq back to idle state */",
            "ds-copy-report")
        if PG13:
            t = rep_once(t,
                "\tfout = CreateArchive(filename, archiveFormat, compressLevel, dosync,\n"
                "\t\t\t\t\t\t archiveMode, setupDumpWorker);",
                "\tfout = CreateArchive(filename, archiveFormat, compressLevel, dosync,\n"
                "\t\t\t\t\t\t archiveMode, setupDumpWorker);\n\n"
                "\tfout->pgdp_stats_enabled = pgdp_stats;",
                "dm-stats-enabled-pg13")
        else:
            t = rep_once(t,
                "\tfout = CreateArchive(filename, archiveFormat, compression_spec,\n"
                "\t\t\t\t\t\t dosync, archiveMode, setupDumpWorker, sync_method);",
                "\tfout = CreateArchive(filename, archiveFormat, compression_spec,\n"
                "\t\t\t\t\t\t dosync, archiveMode, setupDumpWorker, sync_method);\n\n"
                "\tfout->pgdp_stats_enabled = pgdp_stats;",
                "dm-stats-enabled")
        t = rep_once(t,
            "\tint\t\t\trows_this_statement = 0;\n\n\t/* Temporary allows to access to foreign tables to dump data */",
            "\tint\t\t\trows_this_statement = 0;\n\n"
            "\tif (pgdp_stats)\n"
            "\t{\n"
            "\t\tfout->pgdp_stats_active = true;\n"
            "\t\tfout->pgdp_stats_rows_valid = true;\n"
            "\t\tfout->pgdp_stats_rows = 0;\n"
            "\t\tfout->pgdp_stats_bytes = 0;\n"
            "\t}\n\n"
            "\t/* Temporary allows to access to foreign tables to dump data */",
            "ds-insert-state")
        t = rep_once(t,
            "\t\tfor (int tuple = 0; tuple < PQntuples(res); tuple++)\n\t\t{",
            "\t\tfor (int tuple = 0; tuple < PQntuples(res); tuple++)\n\t\t{\n"
            "\t\t\tif (pgdp_stats)\n"
            "\t\t\t{\n"
            "\t\t\t\tif (fout->pgdp_stats_rows == PG_UINT64_MAX)\n"
            f"\t\t\t\t\t{stats_error}(\"--stats INSERT row count overflow for table \\\"%s\\\"\", tbinfo->dobj.name);\n"
            "\t\t\t\tfout->pgdp_stats_rows++;\n"
            "\t\t\t}",
            "ds-insert-rows")
        # 6f: makeTableDataInfo'da dogrulama — filtercond blogunun sonrasina
        anchor6e = "\t\t\ttdinfo->filtercond = psprintf(\"WHERE (%s)\", filter_clause);\n\t}"
        t = rep_once(t, anchor6e, anchor6e + "\n" + MASK_VALIDATE_C.replace("@@FM@@", fm_err), "dm-validate")
        wr(d, t); changed.append(d)

    # ---------- 7) Archive istatistik durumu ve INSERT byte kancasi ----------
    ah = P("src", "bin", "pg_dump", "pg_backup.h"); t = rd(ah)
    t = rep_once(t,
        "\tchar\t   *use_role;\t\t/* Issue SET ROLE to this */\n\n\t/* error handling */",
        "\tchar\t   *use_role;\t\t/* Issue SET ROLE to this */\n\n"
        "\t/* pg_dumpplus: per-table export statistics (worker-local) */\n"
        "\tbool\t\tpgdp_stats_enabled;\n"
        "\tbool\t\tpgdp_stats_active;\n"
        "\tbool\t\tpgdp_stats_rows_valid;\n"
        "\tuint64\t\tpgdp_stats_rows;\n"
        "\tuint64\t\tpgdp_stats_bytes;\n\n"
        "\t/* error handling */",
        "dbh-stats")
    wr(ah, t); changed.append(ah)

    ac = P("src", "bin", "pg_dump", "pg_backup_archiver.c"); t = rd(ac)
    t = rep_once(t,
        "\tWriteData(AH, s, strlen(s));\n}",
        "\tsize_t\t\tlen = strlen(s);\n\n"
        "\tWriteData(AH, s, len);\n"
        "\tif (AH->pgdp_stats_active)\n"
        "\t{\n"
        "\t\tif (AH->pgdp_stats_bytes > UINT64_MAX - (uint64) len)\n"
        f"\t\t\t{stats_error}(\"--stats INSERT byte count overflow\");\n"
        "\t\tAH->pgdp_stats_bytes += (uint64) len;\n"
        "\t}\n}",
        "dba-archputs")
    t = rep_once(t,
        "\tWriteData(AH, p, cnt);\n\tfree(p);\n\treturn (int) cnt;",
        "\tWriteData(AH, p, cnt);\n"
        "\tif (AH->pgdp_stats_active)\n"
        "\t{\n"
        "\t\tif (AH->pgdp_stats_bytes > UINT64_MAX - (uint64) cnt)\n"
        f"\t\t\t{stats_error}(\"--stats INSERT byte count overflow\");\n"
        "\t\tAH->pgdp_stats_bytes += (uint64) cnt;\n"
        "\t}\n"
        "\tfree(p);\n\treturn (int) cnt;",
        "dba-archprintf")
    t = rep_once(t,
        "void\nWriteDataChunksForTocEntry(ArchiveHandle *AH, TocEntry *te)\n{",
        "void\npgdp_report_stats(ArchiveHandle *AH, TocEntry *te)\n{\n"
        "\tif (AH->public.pgdp_stats_active && AH->public.pgdp_stats_rows_valid)\n"
        "\t{\n"
        "\t\tif (AH->public.numWorkers > 1)\n"
        '\t\t\tfprintf(stderr, "pg_dumpplus: table \\\"%s.%s\\\": rows=" UINT64_FORMAT ", bytes=" UINT64_FORMAT "\\n",\n'
        "\t\t\t\t\tte->namespace ? te->namespace : \"\", te->tag ? te->tag : \"\",\n"
        "\t\t\t\t\tAH->public.pgdp_stats_rows, AH->public.pgdp_stats_bytes);\n"
        "\t\telse\n"
        "\t\t\tpg_log_info(\"table \\\"%s.%s\\\": rows=\" UINT64_FORMAT \", bytes=\" UINT64_FORMAT,\n"
        "\t\t\t\t\tte->namespace ? te->namespace : \"\", te->tag ? te->tag : \"\",\n"
        "\t\t\t\t\tAH->public.pgdp_stats_rows, AH->public.pgdp_stats_bytes);\n"
        "\t\tAH->public.pgdp_stats_active = false;\n"
        "\t\tAH->public.pgdp_stats_rows_valid = false;\n"
        "\t}\n}\n\n"
        "void\nWriteDataChunksForTocEntry(ArchiveHandle *AH, TocEntry *te)\n{",
        "dba-report-fn")
    t = rep_once(t,
        "\tif (endPtr != NULL)\n\t\t(*endPtr) (AH, te);\n\n\tAH->currToc = NULL;",
        "\tif (endPtr != NULL)\n\t\t(*endPtr) (AH, te);\n\n"
        "\tpgdp_report_stats(AH, te);\n\n\tAH->currToc = NULL;",
        "dba-report-call")
    wr(ac, t); changed.append(ac)

    ahh = P("src", "bin", "pg_dump", "pg_backup_archiver.h"); t = rd(ahh)
    t = rep_once(t,
        "extern void WriteDataChunksForTocEntry(ArchiveHandle *AH, TocEntry *te);",
        "extern void WriteDataChunksForTocEntry(ArchiveHandle *AH, TocEntry *te);\n"
        "extern void pgdp_report_stats(ArchiveHandle *AH, TocEntry *te);",
        "dbah-proto")
    wr(ahh, t); changed.append(ahh)

    nul = P("src", "bin", "pg_dump", "pg_backup_null.c"); t = rd(nul)
    t = rep_once(t,
        "\t\tte->dataDumper((Archive *) AH, te->dataDumperArg);\n\n\t\tif (strcmp(te->desc, \"BLOBS\") == 0)",
        "\t\tte->dataDumper((Archive *) AH, te->dataDumperArg);\n"
        "\t\tpgdp_report_stats(AH, te);\n\n\t\tif (strcmp(te->desc, \"BLOBS\") == 0)",
        "dbn-report-call")
    wr(nul, t); changed.append(nul)

    # ---------- 8) Makefile: pg_dumpplus hedefi ----------
    mk = P("src","bin","pg_dump","Makefile"); t = rd(mk)
    if "pg_dumpplus" not in t:
        m_link = re.search(r"^pg_dump: [^\n]*\n\t\$\(CC\)[^\n]*-o \$@\S*\n", t, re.M)
        if not m_link: raise Fail("mk-link anchor yok")
        block = m_link.group(0)
        t = t[:m_link.end()] + "# pg_dumpplus: ayni nesnelerden ikinci bagimsiz binary\n" + \
            block.replace("pg_dump:", "pg_dumpplus:", 1) + t[m_link.end():]
        m_inst = re.search(r"^install: all installdirs\n(\t\$\(INSTALL_PROGRAM\) pg_dump[^\n]*\n)", t, re.M)
        if not m_inst: raise Fail("mk-install anchor yok")
        t = t[:m_inst.end(1)] + "\t$(INSTALL_PROGRAM) pg_dumpplus$(X) '$(DESTDIR)$(bindir)'/pg_dumpplus$(X)\n" + t[m_inst.end(1):]
        t = re.sub(r"^all: pg_dump( pg_dumpplus)? pg_restore pg_dumpall$",
                   "all: pg_dump pg_dumpplus pg_restore pg_dumpall", t, flags=re.M)
        m_clean = re.search(r"^clean distclean[^\n]*:$", t, re.M)
        if m_clean:
            t = t[:m_clean.end()] + re.sub(r"rm -f pg_dump\$\(X\)", "rm -f pg_dump$(X) pg_dumpplus$(X)", t[m_clean.end():], count=1)
        wr(mk, t); changed.append(mk)

    state = {
        "patcher": patcher_hash,
        "files": {name: digest(pending[P(name)]) for name in PATCH_FILES},
    }
    written = []
    try:
        for path, content in pending.items():
            atomic_write(path, content)
            written.append(path)
        atomic_write(manifest, (json.dumps(state, sort_keys=True, indent=2) + "\n").encode())
    except OSError:
        for path in reversed(written):
            atomic_write(path, originals[path])
        raise
    print("UYGULANDI:", ", ".join(os.path.relpath(p, root) for p in pending))

if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (Fail, OSError, ValueError) as e:
        print(f"HATA: {e}", file=sys.stderr); sys.exit(1)
