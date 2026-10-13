"""Generate the constraint/index part of docs/04a-relational-schema.md from a live PostgreSQL catalog.

    createdb fbdb_doc && psql fbdb_doc -f sql/001_schema.sql -f sql/002_staging.sql -f sql/003_pipeline_state.sql \
        -f sql/004_export_view.sql -f sql/005_directors_enrichment.sql
    python scripts/gen_schema_doc.py postgresql://user@localhost/fbdb_doc > /tmp/schema_generated.md

Reading the catalog (instead of retyping the DDL) keeps the document identical to what PostgreSQL really enforces.
"""
import sys

import psycopg

CORE = [
    "companies", "company_addresses", "company_contacts", "company_directors", "company_financials",
    "company_certifications", "company_reviews", "data_sources", "company_source_records",
    "company_change_history", "import_export_batches", "data_quality_issues",
]
SUPPORT = ["stg_sirene_ul", "stg_sirene_etab", "pipeline_state", "naf_reference"]


def short(defn: str) -> str:
    return defn.replace("CREATE UNIQUE INDEX ", "UNIQUE ").replace("CREATE INDEX ", "").replace(" ON public.", " ON ")


def main(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        out = ["## Constraints and indexes per table (generated from the PostgreSQL catalog)", ""]
        fks: list[tuple[str, str, str]] = []
        for t in CORE + SUPPORT:
            cols = conn.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (t,)
            ).fetchone()[0]
            out += [f"### {t}", "", f"{cols} columns.", ""]
            cons = conn.execute(
                "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
                "ORDER BY contype, conname", (f"public.{t}",)).fetchall()
            label = {"p": "Primary key", "f": "Foreign key", "u": "Unique", "c": "Check"}
            for ctype, d in cons:
                out.append(f"- **{label.get(ctype, ctype)}**: `{d}`")
                if ctype == "f":
                    ref = d.split("REFERENCES ")[1].split("(")[0]
                    fks.append((t, ref, d))
            idx = conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s "
                "AND indexname NOT IN (SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass) "
                "ORDER BY indexname", (t, f"public.{t}")).fetchall()
            for name, d in idx:
                out.append(f"- **Index** `{name}`: `{short(d).split(' ON ', 1)[1]}`")
            out.append("")
        out += ["## Entity relationships (generated from foreign keys)", "", "```mermaid", "erDiagram"]
        for child, parent, _ in sorted(set(fks)):
            out.append(f"  {parent} ||--o{{ {child} : has")
        out += ["```", ""]
        print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
