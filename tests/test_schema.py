from fbdb.schema import sql_files

NAMES = ["002_staging.sql", "000_reference_data.sql", "001_schema.sql", "README.md", "004_export_view.sql"]


def test_fresh_database_gets_every_file_in_order():
    assert sql_files(False, NAMES) == ["000_reference_data.sql", "001_schema.sql", "002_staging.sql", "004_export_view.sql"]


def test_existing_database_skips_core_tables():
    assert "001_schema.sql" not in sql_files(True, NAMES)


def test_init_db_script_builds_its_list_from_the_sql_folder():
    """init_db.sh once had a hand-written file list that missed 003a_provenance.sql (the export view needs it)."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "scripts" / "init_db.sh").read_text(encoding="utf-8")
    assert "sql/*.sql" in script
    assert "sql/003_pipeline_state.sql sql/004" not in script   # no hardcoded list


def test_provenance_runs_before_the_export_view():
    names = ["004_export_view.sql", "003a_provenance.sql", "003_pipeline_state.sql", "001_schema.sql"]
    assert sql_files(False, names) == ["001_schema.sql", "003_pipeline_state.sql", "003a_provenance.sql", "004_export_view.sql"]
