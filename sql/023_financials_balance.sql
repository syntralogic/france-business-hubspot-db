-- Balance-sheet figures per exercise (spec section 4: capitaux propres, tresorerie, endettement). Idempotent.
-- NULL = the provider did not publish the figure for this exercise. NEVER replaced by zero.
ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS equity_eur NUMERIC(16,2);   -- capitaux propres (equity)
ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS cash_eur   NUMERIC(16,2);   -- tresorerie (cash)
ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS debt_eur   NUMERIC(16,2);   -- endettement (financial debt)
