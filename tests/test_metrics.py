from datetime import date

from fbdb.report.metrics import evaluate, fill_status, render_report, write_outputs


def test_status_rules():
    assert fill_status(0.5, None) == "REPORT"
    assert fill_status(0.99, 0.99) == "PASS" and fill_status(0.98, 0.99) == "GAP"


def test_evaluate_and_report(tmp_path):
    rows = evaluate({"siret": 1.0, "city": 0.90, "phone": 0.12, "unknown_metric": 0.5}, {"siret": 1.0, "city": 0.99, "phone": None})
    by = {r["metric"]: r for r in rows}
    assert by["city"]["status"] == "GAP" and round(by["city"]["gap"], 2) == 0.09
    assert by["phone"]["status"] == "REPORT" and by["unknown_metric"]["status"] == "REPORT"
    m = {"active": 1000, "exportable": 900, "coverage": 0.9, "target_rows": 500000, "volume_vs_target": 0.0018}
    txt = render_report(m, rows, date(2026, 10, 13))
    assert "`city`: 90.0% vs target 99.0%" in txt and "`phone`: 12.0%" in txt and "2026-10-13" in txt
    write_outputs(tmp_path, m, rows)
    assert (tmp_path / "fill_rates.csv").read_text().splitlines()[0] == "metric,rate_pct,target_pct,status,gap_pct"
    assert {p.name for p in tmp_path.iterdir()} == {"metrics.json", "fill_rates.csv", "client_report.md"}
