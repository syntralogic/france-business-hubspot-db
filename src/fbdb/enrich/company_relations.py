"""Documented legal relations between companies (spec section 12, "liens avec d'autres societes").

Only what the register writes: a legal entity (with its own valid SIREN) that holds an office in the company, from the Annuaire
des Entreprises `dirigeants` (type personne morale). Typically a holding or parent company. Partners, clients or brands listed on a
website are NOT relations here and are never matched to a company of our base (see site_signals). Auditors are dropped.

refresh_relations() is called by fbdb.enrich.directors for each company it checks (same API answer, no extra call):
new relation -> inserted and written to company_change_history; a relation that is no longer listed -> is_active=false, ended_on=today.
`related_company_id` is filled when that SIREN is in our base, so a group can be followed in SQL (view v_company_relations).
"""
from __future__ import annotations


def refresh_relations(cur, company_id: int, officers: list[dict], source_id: int, batch_id: int) -> int:
    """Apply one company's legal-entity officers. Returns the number of relations added, reactivated or ended."""
    cur.execute("SELECT relation_id, related_siren, role, is_active FROM company_relations WHERE company_id = %s", (company_id,))
    known = {(r[1], r[2]): (r[0], r[3]) for r in cur.fetchall()}
    wanted = {(o["siren"], o["role"]): o for o in officers}
    changed = 0
    for key, o in wanted.items():
        cur.execute("SELECT company_id FROM companies WHERE siren = %s", (o["siren"],))
        row = cur.fetchone()
        related_id = row[0] if row else None
        if key in known:
            rid, active = known[key]
            cur.execute(
                "UPDATE company_relations SET last_verified_at = now(), related_name = COALESCE(%s, related_name), related_company_id = %s, "
                "is_active = TRUE, ended_on = NULL WHERE relation_id = %s", (o["name"], related_id, rid))
            if not active:
                changed += 1
                _history(cur, company_id, None, key, "insert", source_id, batch_id)
            continue
        cur.execute(
            "INSERT INTO company_relations (company_id, related_siren, related_name, related_company_id, role, source_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)", (company_id, o["siren"], o["name"], related_id, o["role"], source_id))
        _history(cur, company_id, None, key, "insert", source_id, batch_id)
        changed += 1
    for key, (rid, active) in known.items():
        if key in wanted or not active:
            continue
        cur.execute("UPDATE company_relations SET is_active = FALSE, ended_on = current_date, last_verified_at = now() WHERE relation_id = %s", (rid,))
        _history(cur, company_id, key, None, "delete", source_id, batch_id)
        changed += 1
    return changed


def _history(cur, company_id, old, new, change_type, source_id, batch_id) -> None:
    fmt = lambda k: f"{k[0]} ({k[1]})" if k and k[1] else (k[0] if k else None)
    cur.execute(
        "INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, source_id, batch_id) "
        "VALUES (%s, 'company_relations', 'legal_entity_officer', %s, %s, %s, %s, %s)",
        (company_id, fmt(old), fmt(new), change_type, source_id, batch_id))
