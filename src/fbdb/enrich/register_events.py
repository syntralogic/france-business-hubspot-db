"""Agency opening and new activity, read from the REGISTER (spec section 12, "ouverture d'agence / nouvelle activite").

Not read from websites: a site can say anything, the register dates things. Three sources, all kept with their basis in
company_register_events (idempotent, a company is never modified):

  new_establishment  (basis sirene_creation_date) an active, diffusible, non head-office establishment created in the last
                     N days (default 365) while the company already had an older establishment. SIRENE cannot tell a real
                     opening from a transfer or a re-registration: treat it as a SIGNAL to look at, not a proof of expansion.
  activity_change    (basis sirene_change_history) the company's main NAF changed in the weekly delta (company_change_history).
                     A batch that changes far more companies than plausible (> max(1000, 2% of active companies)) is a
                     nomenclature reclassification, not business activity, and produces no event (a warning is logged).
  bodacc_establishment / bodacc_activity (basis bodacc_label) a BODACC notice outside the collective/radiation families whose OWN
                     label (familleavis_lib / typeavis) says an establishment was opened/created or the activity changed. Only the
                     label is read (bodacc_events keeps no notice text). Family codes and labels of the live API are NOT verified
                     from the build sandbox: fetch `BODACC_FAMILIES=collective,radiation,creation,modification`, read a sample of
                     stored labels, then adjust classify_notice(). No label match -> no event.

Usage: python -m fbdb.enrich.register_events [--days 365]. Needs DATABASE_URL.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Optional

import psycopg

from fbdb.transform.normalize import strip_accents

log = logging.getLogger("fbdb.register_events")
EXCLUDED_FAMILIES = ("collective", "radiation")      # handled as data-quality issues by fbdb.enrich.bodacc
MASS_CHANGE_MIN = 1000
MASS_CHANGE_SHARE = 0.02
_OPEN = re.compile(r"\b(ouverture|creation|ajout|installation)\b")
_ACTIVITY = re.compile(r"\b(modification|changement|extension|ajout|nouvelle|nouveau)\b")


def classify_notice(family: Optional[str], family_label: Optional[str], avis_type: Optional[str]) -> Optional[str]:
    """'bodacc_establishment' | 'bodacc_activity' | None, from the notice's own labels only. Pure function."""
    if (family or "").strip().lower() in EXCLUDED_FAMILIES:
        return None
    text = strip_accents(f"{family_label or ''} {avis_type or ''}").lower().replace("'", " ")
    if "etablissement" in text and _OPEN.search(text):
        return "bodacc_establishment"
    if "activite" in text and _ACTIVITY.search(text):
        return "bodacc_activity"
    return None


NEW_ESTABLISHMENT = """
INSERT INTO company_register_events (company_id, establishment_id, event_type, event_date, detail, basis, ref_key)
SELECT a.company_id, a.establishment_id, 'new_establishment', a.creation_date, NULLIF(concat_ws(' ', a.postal_code, a.city), ''),
       'sirene_creation_date', a.siret
FROM company_addresses a JOIN companies c ON c.company_id = a.company_id
WHERE a.status = 'active' AND a.is_diffusible AND NOT a.is_head_office AND a.siret IS NOT NULL
  AND c.status = 'active' AND c.is_diffusible
  AND a.creation_date >= current_date - %(days)s
  AND EXISTS (SELECT 1 FROM company_addresses o WHERE o.company_id = a.company_id
                AND o.establishment_id <> a.establishment_id AND o.creation_date < a.creation_date)
ON CONFLICT (company_id, event_type, ref_key) DO NOTHING
"""

ACTIVITY_CHANGE = """
WITH ch AS (
  SELECT h.change_id, h.company_id, h.batch_id, h.changed_at, h.old_value, h.new_value
  FROM company_change_history h JOIN companies c ON c.company_id = h.company_id
  WHERE h.table_name = 'companies' AND h.field_name = 'main_naf' AND h.change_type = 'update'
    AND h.old_value IS NOT NULL AND h.new_value IS NOT NULL AND h.old_value <> h.new_value
    AND h.changed_at >= now() - make_interval(days => %(days)s) AND c.status = 'active' AND c.is_diffusible
), big AS (
  SELECT batch_id FROM ch WHERE batch_id IS NOT NULL GROUP BY batch_id
  HAVING count(*) > greatest(%(min)s, (SELECT count(*) FROM companies WHERE status = 'active') * %(share)s)
)
INSERT INTO company_register_events (company_id, event_type, event_date, detail, basis, ref_key)
SELECT company_id, 'activity_change', changed_at::date, old_value || ' -> ' || new_value, 'sirene_change_history', change_id::text
FROM ch WHERE batch_id IS NULL OR batch_id NOT IN (SELECT batch_id FROM big)
ON CONFLICT (company_id, event_type, ref_key) DO NOTHING
"""

MASS_BATCHES = """
SELECT h.batch_id, count(*) FROM company_change_history h
WHERE h.table_name = 'companies' AND h.field_name = 'main_naf' AND h.change_type = 'update'
  AND h.changed_at >= now() - make_interval(days => %(days)s) AND h.batch_id IS NOT NULL
GROUP BY h.batch_id HAVING count(*) > greatest(%(min)s, (SELECT count(*) FROM companies WHERE status = 'active') * %(share)s)
"""


def run(dsn: str, days: int = 365) -> dict:
    stats = {"new_establishment": 0, "activity_change": 0, "bodacc_establishment": 0, "bodacc_activity": 0, "mass_batches_ignored": 0}
    p = {"days": days, "min": MASS_CHANGE_MIN, "share": MASS_CHANGE_SHARE}
    with psycopg.connect(dsn) as conn:
        stats["new_establishment"] = conn.execute(NEW_ESTABLISHMENT, p).rowcount
        for batch_id, n in conn.execute(MASS_BATCHES, p).fetchall():
            stats["mass_batches_ignored"] += 1
            log.warning("batch %s changed the main NAF of %s companies: looks like a reclassification, no activity events", batch_id, n)
        stats["activity_change"] = conn.execute(ACTIVITY_CHANGE, p).rowcount
        rows = conn.execute(
            """SELECT b.bodacc_id, b.family, b.family_label, b.avis_type, b.published_on, c.company_id
               FROM bodacc_events b JOIN companies c ON c.siren = b.siren
               WHERE b.family NOT IN ('collective','radiation') AND b.published_on >= current_date - %s
                 AND c.status = 'active' AND c.is_diffusible""", (days,)).fetchall()
        for bid, fam, label, avis, pub, company_id in rows:
            kind = classify_notice(fam, label, avis)
            if not kind:
                continue
            stats[kind] += conn.execute(
                """INSERT INTO company_register_events (company_id, event_type, event_date, detail, basis, ref_key)
                   VALUES (%s,%s,%s,%s,'bodacc_label',%s) ON CONFLICT (company_id, event_type, ref_key) DO NOTHING""",
                (company_id, kind, pub, (label or avis or fam), bid)).rowcount
        conn.commit()
    log.info("register events %s", stats)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(os.environ["DATABASE_URL"], a.days))


if __name__ == "__main__":
    main()
