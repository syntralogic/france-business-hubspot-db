-- Two more facets for what a site says (client spec section 3): types of buildings / sites worked on, and the commitments
-- the company announces (delays, free quote, guarantee, support, specialisation). Idempotent.
ALTER TABLE company_activities DROP CONSTRAINT IF EXISTS company_activities_facet_check;
ALTER TABLE company_activities ADD CONSTRAINT company_activities_facet_check CHECK (facet IN
  ('trade','service','specialty','work_type','client_type','contract','brand','approval','building_type','commitment'));
