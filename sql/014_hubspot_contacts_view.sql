-- HubSpot CONTACTS export (people), one row per active interlocutor that has at least one usable email/phone/mobile.
-- Same rules as the company view: only 'verified'/'sourced' values, nothing opposed on the channel (email -> email, phone -> call),
-- legacy opposed_at honoured. A phone starting +336/+337 goes to "mobile", any other phone to "phone" (prefix hint, not a proof of
-- ownership: the evidence level of each value is exported next to it). Only values LINKED to the person are used.
CREATE OR REPLACE VIEW v_hubspot_contacts AS
WITH usable AS (
  SELECT k.interlocutor_id, k.contact_type, k.value_normalized AS value, k.attribution_level, k.is_primary, k.contact_id,
         CASE WHEN k.contact_type = 'phone' AND k.value_normalized ~ '^\+33[67]' THEN 'mobile' ELSE k.contact_type END AS slot
  FROM company_contacts k
  WHERE k.interlocutor_id IS NOT NULL AND k.contact_type IN ('phone','email') AND k.value_status IN ('verified','sourced')
    AND k.opposed_at IS NULL
    AND NOT EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = k.contact_type AND o.value_normalized = k.value_normalized
                    AND o.channel = CASE WHEN k.contact_type = 'email' THEN 'email' ELSE 'call' END)
), best AS (
  SELECT DISTINCT ON (interlocutor_id, slot) interlocutor_id, slot, value, attribution_level
  FROM usable
  ORDER BY interlocutor_id, slot, CASE attribution_level WHEN 'confirmed_by_contact' THEN 0 WHEN 'published_with_name' THEN 1 ELSE 2 END, is_primary DESC, contact_id
)
SELECT i.interlocutor_id, c.siren, c.legal_name AS company_name,
       (SELECT w.value_normalized FROM company_contacts w WHERE w.company_id = c.company_id AND w.contact_type = 'website'
          AND w.value_status IN ('verified','sourced') ORDER BY (w.value_status = 'verified') DESC, w.is_primary DESC, w.contact_id LIMIT 1) AS company_domain,
       i.first_name, i.last_name, coalesce(i.operational_function, i.legal_role) AS job_title,
       i.function_category, i.decision_maker_confirmed,
       e.value AS email, e.attribution_level AS email_evidence,
       p.value AS phone, p.attribution_level AS phone_evidence,
       m.value AS mobile, m.attribution_level AS mobile_evidence
FROM company_interlocutors i
JOIN companies c ON c.company_id = i.company_id AND c.status = 'active' AND c.is_diffusible
LEFT JOIN best e ON e.interlocutor_id = i.interlocutor_id AND e.slot = 'email'
LEFT JOIN best p ON p.interlocutor_id = i.interlocutor_id AND p.slot = 'phone'
LEFT JOIN best m ON m.interlocutor_id = i.interlocutor_id AND m.slot = 'mobile'
WHERE i.is_active AND (e.value IS NOT NULL OR p.value IS NOT NULL OR m.value IS NOT NULL);
