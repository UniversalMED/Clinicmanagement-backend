-- Fix: enable RLS on lab_tests and audit_logs
-- Run this in Supabase SQL Editor if those two tables still show rowsecurity = false

ALTER TABLE public.lab_tests ENABLE ROW LEVEL SECURITY;

CREATE POLICY "lab_tests: clinic isolation"
ON public.lab_tests
FOR ALL
USING (clinic_id = public.current_clinic_id())
WITH CHECK (clinic_id = public.current_clinic_id());


ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "audit_logs: clinic isolation"
ON public.audit_logs
FOR ALL
USING (clinic_id = public.current_clinic_id())
WITH CHECK (clinic_id = public.current_clinic_id());


-- Verify
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
