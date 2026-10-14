from fbdb.schema import sql_files

NAMES = ["002_staging.sql", "000_reference_data.sql", "001_schema.sql", "README.md", "004_export_view.sql"]


def test_fresh_database_gets_every_file_in_order():
    assert sql_files(False, NAMES) == ["000_reference_data.sql", "001_schema.sql", "002_staging.sql", "004_export_view.sql"]


def test_existing_database_skips_core_tables():
    assert "001_schema.sql" not in sql_files(True, NAMES)
