-- Diffusion status of the ESTABLISHMENT (INSEE statutDiffusionEtablissement), not only of the company (spec section 1).
-- Numbered 003c because 004_export_view.sql reads it and all files run in order at every start. Idempotent.
-- An establishment that INSEE later marks partially / not diffusible is not deleted (the weekly load skips it), but it must
-- stop being exported or sent to any provider: is_diffusible=FALSE hides it from the HubSpot view and from the enrichment
-- selections. When INSEE diffuses it again the weekly load sets it back to TRUE and refreshes its fields.
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS is_diffusible BOOLEAN NOT NULL DEFAULT TRUE;
