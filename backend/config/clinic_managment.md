-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

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
CREATE TABLE public.test_orders (
  id uuid NOT NULL DEFAULT uuid_generate_v4(),
  visit_id uuid,
  consultation_id uuid,
  test_id uuid,
  status text DEFAULT 'pending'::text CHECK (status = ANY (ARRAY['pending'::text, 'in_progress'::text, 'completed'::text])),
  ordered_by uuid,
  created_at timestamp without time zone DEFAULT now(),
  assigned_to uuid,
  CONSTRAINT test_orders_pkey PRIMARY KEY (id),
  CONSTRAINT test_orders_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES public.visits(id),
  CONSTRAINT test_orders_consultation_id_fkey FOREIGN KEY (consultation_id) REFERENCES public.consultations(id),
  CONSTRAINT test_orders_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.lab_tests(id),
  CONSTRAINT test_orders_ordered_by_fkey FOREIGN KEY (ordered_by) REFERENCES public.profiles(id),
  CONSTRAINT test_orders_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.profiles(id)
);
-- Billing fields added to test_orders (run as migration in Supabase)
ALTER TABLE public.test_orders
  ADD COLUMN IF NOT EXISTS is_billable         boolean       NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS price_at_order_time numeric(10,2) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS billed_invoice_id   uuid          REFERENCES public.invoices(id);
-- Update status constraint to include canceled
ALTER TABLE public.test_orders DROP CONSTRAINT IF EXISTS test_orders_status_check;
ALTER TABLE public.test_orders ADD CONSTRAINT test_orders_status_check
  CHECK (status = ANY (ARRAY['pending','in_progress','completed','canceled']));
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
CREATE TABLE public.invoices (
  id              uuid          NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id       uuid          NOT NULL REFERENCES public.clinics(id),
  visit_id        uuid          NOT NULL REFERENCES public.visits(id),
  patient_id      uuid          NOT NULL REFERENCES public.patients(id),
  issued_by       uuid          REFERENCES public.profiles(id),
  finalized_by    uuid          REFERENCES public.profiles(id),
  voided_by       uuid          REFERENCES public.profiles(id),
  status          text          NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','finalized','void')),
  subtotal        numeric(10,2) NOT NULL DEFAULT 0,
  discount_amount numeric(10,2) NOT NULL DEFAULT 0,
  total_amount    numeric(10,2) NOT NULL DEFAULT 0,
  notes           text,
  finalized_at    timestamp,
  voided_at       timestamp,
  void_reason     text,
  created_at      timestamp     NOT NULL DEFAULT now(),
  CONSTRAINT invoices_pkey PRIMARY KEY (id),
  CONSTRAINT invoices_void_requires_reason CHECK (status != 'void' OR void_reason IS NOT NULL),
  CONSTRAINT invoices_totals_non_negative CHECK (subtotal >= 0 AND discount_amount >= 0 AND total_amount >= 0)
);
CREATE TABLE public.invoice_line_items (
  id            uuid          NOT NULL DEFAULT uuid_generate_v4(),
  invoice_id    uuid          NOT NULL REFERENCES public.invoices(id),
  test_order_id uuid          REFERENCES public.test_orders(id),
  test_name     text          NOT NULL,
  unit_price    numeric(10,2) NOT NULL CHECK (unit_price >= 0),
  quantity      integer       NOT NULL DEFAULT 1 CHECK (quantity > 0),
  subtotal      numeric(10,2) NOT NULL CHECK (subtotal = unit_price * quantity),
  notes         text,
  created_at    timestamp     NOT NULL DEFAULT now(),
  CONSTRAINT invoice_line_items_pkey PRIMARY KEY (id),
  CONSTRAINT no_duplicate_test_order_billing UNIQUE (invoice_id, test_order_id)
);

-- ============================================================
-- Patient Flow: Appointments, Queue Entries, State Audit
-- Run these in Supabase SQL editor
-- ============================================================

CREATE TABLE public.appointments (
  id               uuid          NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id        uuid          NOT NULL REFERENCES public.clinics(id),
  patient_id       uuid          NOT NULL REFERENCES public.patients(id),
  doctor_id        uuid          REFERENCES public.profiles(id),
  scheduled_at     timestamptz   NOT NULL,
  duration_minutes integer       NOT NULL DEFAULT 30,
  type             text          NOT NULL CHECK (type IN ('specialist','general')),
  notes            text,
  status           text          NOT NULL DEFAULT 'active'
                                 CHECK (status IN ('active','cancelled','rescheduled','affected')),
  cancelled_at     timestamptz,
  cancelled_by     uuid          REFERENCES public.profiles(id),
  cancel_reason    text,
  created_at       timestamptz   NOT NULL DEFAULT now(),
  CONSTRAINT appointments_pkey PRIMARY KEY (id)
);

CREATE INDEX idx_appointments_clinic_scheduled ON public.appointments(clinic_id, scheduled_at);
CREATE INDEX idx_appointments_doctor ON public.appointments(doctor_id, scheduled_at);

CREATE TABLE public.queue_entries (
  id                   uuid        NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id            uuid        NOT NULL REFERENCES public.clinics(id),
  patient_id           uuid        NOT NULL REFERENCES public.patients(id),
  appointment_id       uuid        REFERENCES public.appointments(id),
  visit_id             uuid        REFERENCES public.visits(id),
  status               text        NOT NULL DEFAULT 'checked_in'
                                   CHECK (status IN (
                                     'scheduled','checked_in','waiting',
                                     'called','in_progress','completed','no_show'
                                   )),
  queue_position       integer,
  entry_type           text        NOT NULL CHECK (entry_type IN ('appointment','walk_in')),
  priority_override    integer     NOT NULL DEFAULT 0,
  scheduled_at         timestamptz,
  checked_in_at        timestamptz,
  called_at            timestamptz,
  in_progress_at       timestamptz,
  completed_at         timestamptz,
  no_show_at           timestamptz,
  grace_period_ends_at timestamptz,
  call_timeout_at      timestamptz,
  assigned_doctor_id   uuid        REFERENCES public.profiles(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT queue_entries_pkey PRIMARY KEY (id)
);

-- Prevent duplicate active queue entries for the same patient in a clinic
CREATE UNIQUE INDEX idx_one_active_queue_per_patient
  ON public.queue_entries(clinic_id, patient_id)
  WHERE status NOT IN ('completed', 'no_show');

CREATE INDEX idx_queue_clinic_waiting
  ON public.queue_entries(clinic_id, queue_position)
  WHERE status = 'waiting';

CREATE TABLE public.queue_state_audit (
  id               uuid        NOT NULL DEFAULT uuid_generate_v4(),
  queue_entry_id   uuid        NOT NULL REFERENCES public.queue_entries(id),
  clinic_id        uuid        NOT NULL,
  patient_id       uuid        NOT NULL,
  previous_status  text,
  new_status       text        NOT NULL,
  changed_by       uuid        REFERENCES public.profiles(id),
  change_reason    text,
  metadata         jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT queue_state_audit_pkey PRIMARY KEY (id)
);

CREATE INDEX idx_queue_audit_entry ON public.queue_state_audit(queue_entry_id);
CREATE INDEX idx_queue_audit_clinic ON public.queue_state_audit(clinic_id, created_at DESC);
-- ============================================================
-- Notifications
-- ============================================================

CREATE TABLE public.notifications (
  id            uuid        NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id     uuid        NOT NULL,
  recipient_id  uuid        NOT NULL REFERENCES public.profiles(id),
  event_type    text        NOT NULL CHECK (event_type IN (
                    'lab_test_requested', 'lab_test_started', 'lab_test_completed')),
  entity_type   text        NOT NULL DEFAULT 'test_order',
  entity_id     uuid        NOT NULL,
  payload       jsonb       NOT NULL DEFAULT '{}',
  status        text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'delivered', 'failed')),
  retry_count   int         NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  delivered_at  timestamptz,
  CONSTRAINT notifications_pkey PRIMARY KEY (id),
  CONSTRAINT notifications_no_duplicate UNIQUE (recipient_id, event_type, entity_id)
);

CREATE INDEX idx_notifications_recipient_status
  ON public.notifications(recipient_id, status);
CREATE INDEX idx_notifications_clinic
  ON public.notifications(clinic_id);

-- ============================================================
-- Clinics (super admin tenant registry)
-- ============================================================

CREATE TABLE public.clinics (
  id         uuid        NOT NULL DEFAULT uuid_generate_v4(),
  name       text        NOT NULL,
  slug       text        NOT NULL,
  is_active  boolean     NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT clinics_pkey        PRIMARY KEY (id),
  CONSTRAINT clinics_name_unique UNIQUE (name),
  CONSTRAINT clinics_slug_unique UNIQUE (slug)
);

CREATE INDEX idx_clinics_slug ON public.clinics(slug);

-- ============================================================
-- Payments (Chapa payment integration)
-- ============================================================

CREATE TABLE public.payments (
  id           uuid          NOT NULL DEFAULT uuid_generate_v4(),
  clinic_id    uuid          NOT NULL REFERENCES public.clinics(id),
  invoice_id   uuid          NOT NULL REFERENCES public.invoices(id),
  initiated_by uuid          REFERENCES public.profiles(id),
  tx_ref       text          NOT NULL UNIQUE,          -- our reference sent to Chapa
  chapa_ref    text,                                   -- Chapa's own reference
  amount       numeric(10,2) NOT NULL,
  currency     text          NOT NULL DEFAULT 'ETB',
  status       text          NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','success','failed')),
  mode         text          NOT NULL DEFAULT 'test',  -- 'test' | 'live'
  paid_at      timestamptz,
  created_at   timestamptz   NOT NULL DEFAULT now(),
  CONSTRAINT payments_pkey PRIMARY KEY (id)
);

CREATE INDEX idx_payments_invoice  ON public.payments(invoice_id);
CREATE INDEX idx_payments_clinic   ON public.payments(clinic_id);
CREATE INDEX idx_payments_tx_ref   ON public.payments(tx_ref);
