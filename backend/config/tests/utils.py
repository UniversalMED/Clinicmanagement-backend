"""
Shared test utilities — factories and JWT helpers.

All tests use force_authenticate(user=JWTUser(...)) so they test view and
permission logic without going through the JWT middleware. Auth middleware
behaviour is tested separately in test_auth.py using real encoded tokens.
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import jwt as pyjwt
from rest_framework.test import APIClient

from django.utils import timezone

from clinic.models import Patient, Visit, Consultation, Prescription, PrescriptionItem
from core.authentication import JWTUser
from lab.models import LabTest, TestOrder, TestResult
from billing.models import Invoice, InvoiceLineItem
from patient_flow.models import Appointment, QueueEntry, QueueStateAudit
from users.models import Profile

# Fixed secret used for all auth middleware tests — overridden via
# @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET) in each test class.
TEST_JWT_SECRET = 'test-secret-for-tests-only'


# ---------------------------------------------------------------------------
# User / profile factories
# ---------------------------------------------------------------------------

def make_clinic_id():
    return uuid.uuid4()


def make_user(clinic_id, role, full_name=None):
    """Create and return a Profile in the test database."""
    return Profile.objects.create(
        id=uuid.uuid4(),
        clinic_id=clinic_id,
        full_name=full_name or f'{role}-user',
        role=role,
    )


def auth_client(user):
    """
    Return an APIClient authenticated as *user* (a Profile instance).
    Uses force_authenticate so no JWT encoding/decoding is involved —
    request.user is set to a JWTUser built from the profile's fields.
    """
    client = APIClient()
    jwt_user = JWTUser(
        user_id=user.id,
        clinic_id=user.clinic_id,
        role=user.role,
    )
    client.force_authenticate(user=jwt_user)
    return client


# ---------------------------------------------------------------------------
# JWT factory (for auth middleware tests only)
# ---------------------------------------------------------------------------

def make_token(user_id, clinic_id, role, expired=False, secret=None, extra_claims=None):
    """
    Encode a HS256 JWT that mirrors what Supabase issues after the
    custom_access_token_hook injects clinic_id and user_role.
    """
    secret = secret or TEST_JWT_SECRET
    now = datetime.now(dt_timezone.utc)
    payload = {
        'sub': str(user_id),
        'clinic_id': str(clinic_id),
        'user_role': role,
        'aud': 'authenticated',
        'iat': now,
        'exp': (now - timedelta(hours=1)) if expired else (now + timedelta(hours=1)),
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, secret, algorithm='HS256')


# ---------------------------------------------------------------------------
# Clinic / lab factories
# ---------------------------------------------------------------------------

def make_patient(clinic_id, full_name=None):
    return Patient.objects.create(
        clinic_id=clinic_id,
        full_name=full_name or 'Test Patient',
        gender='male',
    )


def make_visit(clinic_id, patient, created_by):
    return Visit.objects.create(
        clinic_id=clinic_id,
        patient_id=patient.id,
        created_by=created_by.id,
        status='open',
    )


def make_consultation(visit, doctor):
    return Consultation.objects.create(
        visit_id=visit.id,
        doctor_id=doctor.id,
        symptoms='headache',
        diagnosis='migraine',
    )


def make_lab_test(clinic_id, created_by):
    return LabTest.objects.create(
        clinic_id=clinic_id,
        name='CBC',
        price='25.00',
        is_active=True,
        created_by=created_by.id,
    )


def make_test_order(visit, lab_test, ordered_by, status='pending'):
    return TestOrder.objects.create(
        visit_id=visit.id,
        test_id=lab_test.id,
        ordered_by=ordered_by.id,
        status=status,
        price_at_order_time=lab_test.price,
    )


def make_invoice(clinic_id, visit, issued_by):
    return Invoice.objects.create(
        clinic_id=clinic_id,
        visit_id=visit.id,
        patient_id=visit.patient_id,
        issued_by=issued_by.id,
    )


def make_line_item(invoice, test_order, lab_test):
    return InvoiceLineItem.objects.create(
        invoice_id=invoice.id,
        test_order_id=test_order.id,
        test_name=lab_test.name,
        unit_price=test_order.price_at_order_time,
        quantity=1,
        subtotal=test_order.price_at_order_time,
    )


def make_prescription(consultation, prescribed_by, notes=''):
    prescription = Prescription.objects.create(
        consultation_id=consultation.id,
        prescribed_by=prescribed_by.id,
        notes=notes,
    )
    PrescriptionItem.objects.create(
        prescription_id=prescription.id,
        medication='Paracetamol',
        dosage='500mg',
        frequency='3x daily',
    )
    return prescription


# ---------------------------------------------------------------------------
# Queue / appointment factories
# ---------------------------------------------------------------------------

def make_appointment(clinic_id, patient, doctor=None, scheduled_at=None, appt_type='specialist'):
    return Appointment.objects.create(
        clinic_id=clinic_id,
        patient_id=patient.id,
        doctor_id=doctor.id if doctor else None,
        scheduled_at=scheduled_at or (timezone.now() + timezone.timedelta(hours=1)),
        type=appt_type,
        status='active',
    )


def make_queue_entry(clinic_id, patient, status='waiting', entry_type='walk_in',
                     appointment=None, doctor=None, queue_position=1):
    """
    Create a QueueEntry directly in any status for test setup.
    Bypasses the full check-in flow.
    """
    entry = QueueEntry.objects.create(
        clinic_id=clinic_id,
        patient_id=patient.id,
        appointment_id=appointment.id if appointment else None,
        status=status,
        entry_type=entry_type,
        queue_position=queue_position if status == 'waiting' else None,
        assigned_doctor_id=doctor.id if doctor else None,
        checked_in_at=timezone.now(),
    )
    QueueStateAudit.objects.create(
        queue_entry_id=entry.id,
        clinic_id=clinic_id,
        patient_id=patient.id,
        previous_status=None,
        new_status=status,
        changed_by=None,
        change_reason='test_setup',
    )
    return entry
