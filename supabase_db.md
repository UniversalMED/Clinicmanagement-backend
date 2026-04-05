-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.appointments (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  patient_id uuid NOT NULL,
  doctor_id uuid,
  scheduled_at timestamp with time zone NOT NULL,
  duration_minutes integer NOT NULL DEFAULT 30,
  type text NOT NULL CHECK (type = ANY (ARRAY['specialist'::text, 'general'::text])),
  notes text,
  status text NOT NULL DEFAULT 'active'::text CHECK (status = ANY (ARRAY['active'::text, 'cancelled'::text, 'rescheduled'::text, 'affected'::text])),
  cancelled_at timestamp with time zone,
  cancelled_by uuid,
  cancel_reason text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT appointments_pkey PRIMARY KEY (id),
  CONSTRAINT appointments_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id),
  CONSTRAINT appointments_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id),
  CONSTRAINT appointments_doctor_id_fkey FOREIGN KEY (doctor_id) REFERENCES public.profiles(id),
  CONSTRAINT appointments_cancelled_by_fkey FOREIGN KEY (cancelled_by) REFERENCES public.profiles(id)
);
CREATE TABLE public.audit_logs (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid,
  user_id uuid,
  action text,
  entity_type text,
  entity_id uuid,
  timestamp timestamp without time zone DEFAULT now(),
  CONSTRAINT audit_logs_pkey PRIMARY KEY (id),
  CONSTRAINT audit_logs_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id),
  CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.clinics (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  name text NOT NULL,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT clinics_pkey PRIMARY KEY (id)
);
CREATE TABLE public.consultations (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  visit_id uuid,
  doctor_id uuid,
  symptoms text,
  diagnosis text,
  notes text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT consultations_pkey PRIMARY KEY (id),
  CONSTRAINT consultations_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES public.visits(id),
  CONSTRAINT consultations_doctor_id_fkey FOREIGN KEY (doctor_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.invoice_line_items (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  invoice_id uuid NOT NULL,
  test_order_id uuid,
  test_name text NOT NULL,
  unit_price numeric NOT NULL CHECK (unit_price >= 0::numeric),
  quantity integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
  subtotal numeric NOT NULL,
  notes text,
  created_at timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT invoice_line_items_pkey PRIMARY KEY (id),
  CONSTRAINT invoice_line_items_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id),
  CONSTRAINT invoice_line_items_test_order_id_fkey FOREIGN KEY (test_order_id) REFERENCES public.test_orders(id)
);
CREATE TABLE public.invoices (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  visit_id uuid NOT NULL,
  patient_id uuid NOT NULL,
  issued_by uuid,
  finalized_by uuid,
  voided_by uuid,
  status text NOT NULL DEFAULT 'draft'::text CHECK (status = ANY (ARRAY['draft'::text, 'finalized'::text, 'void'::text])),
  subtotal numeric NOT NULL DEFAULT 0,
  discount_amount numeric NOT NULL DEFAULT 0,
  total_amount numeric NOT NULL DEFAULT 0,
  notes text,
  finalized_at timestamp without time zone,
  voided_at timestamp without time zone,
  void_reason text,
  created_at timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT invoices_pkey PRIMARY KEY (id),
  CONSTRAINT invoices_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES public.visits(id),
  CONSTRAINT invoices_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id),
  CONSTRAINT invoices_issued_by_fkey FOREIGN KEY (issued_by) REFERENCES public.profiles(id),
  CONSTRAINT invoices_finalized_by_fkey FOREIGN KEY (finalized_by) REFERENCES public.profiles(id),
  CONSTRAINT invoices_voided_by_fkey FOREIGN KEY (voided_by) REFERENCES public.profiles(id),
  CONSTRAINT invoices_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id)
);
CREATE TABLE public.lab_tests (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid,
  name text NOT NULL,
  description text,
  price numeric DEFAULT 0,
  is_active boolean DEFAULT true,
  created_by uuid,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT lab_tests_pkey PRIMARY KEY (id),
  CONSTRAINT lab_tests_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id),
  CONSTRAINT lab_tests_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id)
);
CREATE TABLE public.notifications (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  recipient_id uuid NOT NULL,
  event_type text NOT NULL CHECK (event_type = ANY (ARRAY['lab_test_requested'::text, 'lab_test_started'::text, 'lab_test_completed'::text])),
  entity_type text NOT NULL DEFAULT 'test_order'::text,
  entity_id uuid NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending'::text CHECK (status = ANY (ARRAY['pending'::text, 'delivered'::text, 'failed'::text])),
  retry_count integer NOT NULL DEFAULT 0,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  delivered_at timestamp with time zone,
  CONSTRAINT notifications_pkey PRIMARY KEY (id),
  CONSTRAINT notifications_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.patients (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  full_name text NOT NULL,
  gender text,
  date_of_birth date,
  phone text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT patients_pkey PRIMARY KEY (id),
  CONSTRAINT patients_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id)
);
CREATE TABLE public.prescription_items (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  prescription_id uuid NOT NULL,
  medication text NOT NULL,
  dosage text NOT NULL,
  frequency text NOT NULL,
  duration text,
  instructions text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT prescription_items_pkey PRIMARY KEY (id),
  CONSTRAINT prescription_items_prescription_id_fkey FOREIGN KEY (prescription_id) REFERENCES public.prescriptions(id)
);
CREATE TABLE public.prescriptions (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  consultation_id uuid,
  prescribed_by uuid,
  notes text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT prescriptions_pkey PRIMARY KEY (id),
  CONSTRAINT prescriptions_consultation_id_fkey FOREIGN KEY (consultation_id) REFERENCES public.consultations(id),
  CONSTRAINT prescriptions_prescribed_by_fkey FOREIGN KEY (prescribed_by) REFERENCES public.profiles(id)
);
CREATE TABLE public.profiles (
  id uuid NOT NULL,
  clinic_id uuid NOT NULL,
  full_name text,
  role text CHECK (role = ANY (ARRAY['receptionist'::text, 'doctor'::text, 'lab_tech'::text, 'admin'::text])),
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT profiles_pkey PRIMARY KEY (id),
  CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id),
  CONSTRAINT profiles_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id)
);
CREATE TABLE public.queue_entries (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  patient_id uuid NOT NULL,
  appointment_id uuid,
  visit_id uuid,
  status text NOT NULL DEFAULT 'checked_in'::text CHECK (status = ANY (ARRAY['scheduled'::text, 'checked_in'::text, 'waiting'::text, 'called'::text, 'in_progress'::text, 'completed'::text, 'no_show'::text])),
  queue_position integer,
  entry_type text NOT NULL CHECK (entry_type = ANY (ARRAY['appointment'::text, 'walk_in'::text])),
  priority_override integer NOT NULL DEFAULT 0,
  scheduled_at timestamp with time zone,
  checked_in_at timestamp with time zone,
  called_at timestamp with time zone,
  in_progress_at timestamp with time zone,
  completed_at timestamp with time zone,
  no_show_at timestamp with time zone,
  grace_period_ends_at timestamp with time zone,
  call_timeout_at timestamp with time zone,
  assigned_doctor_id uuid,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT queue_entries_pkey PRIMARY KEY (id),
  CONSTRAINT queue_entries_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id),
  CONSTRAINT queue_entries_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id),
  CONSTRAINT queue_entries_appointment_id_fkey FOREIGN KEY (appointment_id) REFERENCES public.appointments(id),
  CONSTRAINT queue_entries_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES public.visits(id),
  CONSTRAINT queue_entries_assigned_doctor_id_fkey FOREIGN KEY (assigned_doctor_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.queue_state_audit (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  queue_entry_id uuid NOT NULL,
  clinic_id uuid NOT NULL,
  patient_id uuid NOT NULL,
  previous_status text,
  new_status text NOT NULL,
  changed_by uuid,
  change_reason text,
  metadata jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT queue_state_audit_pkey PRIMARY KEY (id),
  CONSTRAINT queue_state_audit_queue_entry_id_fkey FOREIGN KEY (queue_entry_id) REFERENCES public.queue_entries(id),
  CONSTRAINT queue_state_audit_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.profiles(id)
);
CREATE TABLE public.test_orders (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  visit_id uuid,
  consultation_id uuid,
  test_id uuid,
  status text DEFAULT 'pending'::text,
  ordered_by uuid,
  created_at timestamp without time zone DEFAULT now(),
  assigned_to uuid,
  is_billable boolean NOT NULL DEFAULT true,
  price_at_order_time numeric NOT NULL DEFAULT 0,
  CONSTRAINT test_orders_pkey PRIMARY KEY (id),
  CONSTRAINT test_orders_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES public.visits(id),
  CONSTRAINT test_orders_consultation_id_fkey FOREIGN KEY (consultation_id) REFERENCES public.consultations(id),
  CONSTRAINT test_orders_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.lab_tests(id),
  CONSTRAINT test_orders_ordered_by_fkey FOREIGN KEY (ordered_by) REFERENCES public.profiles(id),
  CONSTRAINT test_orders_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.profiles(id)
);
CREATE TABLE public.test_results (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  test_order_id uuid,
  technician_id uuid,
  result_data jsonb,
  remarks text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT test_results_pkey PRIMARY KEY (id),
  CONSTRAINT test_results_test_order_id_fkey FOREIGN KEY (test_order_id) REFERENCES public.test_orders(id),
  CONSTRAINT test_results_technician_id_fkey FOREIGN KEY (technician_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.visits (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id uuid NOT NULL,
  patient_id uuid,
  created_by uuid,
  status text DEFAULT 'open'::text CHECK (status = ANY (ARRAY['open'::text, 'in_progress'::text, 'completed'::text])),
  created_at timestamp without time zone DEFAULT now(),
  assigned_doctor_id uuid,
  CONSTRAINT visits_pkey PRIMARY KEY (id),
  CONSTRAINT visits_clinic_id_fkey FOREIGN KEY (clinic_id) REFERENCES public.clinics(id),
  CONSTRAINT visits_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id),
  CONSTRAINT visits_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id),
  CONSTRAINT visits_assigned_doctor_id_fkey FOREIGN KEY (assigned_doctor_id) REFERENCES public.profiles(id)
);