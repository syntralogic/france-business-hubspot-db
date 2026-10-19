-- The function of a mailbox can also be declared by the provider file (its column says "devis", "direction"...).
-- It is a fourth basis next to address_name / page_context / manual. Idempotent.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'company_contacts_contact_use_basis_check') THEN
    ALTER TABLE company_contacts DROP CONSTRAINT company_contacts_contact_use_basis_check;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_contact_use_basis_values') THEN
    ALTER TABLE company_contacts ADD CONSTRAINT ck_contact_use_basis_values
      CHECK (contact_use_basis IS NULL OR contact_use_basis IN ('address_name','page_context','provider_file','manual'));
  END IF;
END $$;
