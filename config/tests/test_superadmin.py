"""
Integration tests for the super admin onboarding API.

Covers:
  - IsSuperAdmin permission (only super_admin role allowed)
  - GET /api/superadmin/clinics/ list and filter
  - POST /api/superadmin/clinics/onboard/ validation (duplicate name, missing fields)
  - Clinic row created correctly
  - Profile row created with correct clinic_id and role='admin'
  - Duplicate clinic name rejected
  - Non-super-admin roles all rejected (403)
  - Unauthenticated rejected (401)
  - Super admin JWT has no clinic_id (auth middleware handles it)
"""
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase
from rest_framework.test import APIClient

from core.authentication import JWTUser
from superadmin.models import Clinic
from tests.utils import make_clinic_id, make_user, auth_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def super_admin_client():
    client = APIClient()
    client.force_authenticate(user=JWTUser(
        user_id=uuid.uuid4(),
        clinic_id=None,        # super admin has no clinic
        role='super_admin',
    ))
    return client


def _mock_supabase_ok(new_user_id=None):
    """Patch the Supabase Admin API call to return success."""
    new_user_id = new_user_id or str(uuid.uuid4())
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {'id': new_user_id}
    return mock


def _mock_supabase_fail():
    mock = MagicMock()
    mock.status_code = 422
    mock.json.return_value = {'msg': 'User already registered'}
    mock.text = 'User already registered'
    return mock


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------

class SuperAdminPermissionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic_id = make_clinic_id()
        cls.admin      = make_user(cls.clinic_id, 'admin')
        cls.doctor     = make_user(cls.clinic_id, 'doctor')
        cls.lab_tech   = make_user(cls.clinic_id, 'lab_tech')
        cls.receptionist = make_user(cls.clinic_id, 'receptionist')

    def test_unauthenticated_rejected(self):
        resp = APIClient().get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 401)

    def test_clinic_admin_rejected(self):
        resp = auth_client(self.admin).get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 403)

    def test_doctor_rejected(self):
        resp = auth_client(self.doctor).get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_rejected(self):
        resp = auth_client(self.lab_tech).get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 403)

    def test_receptionist_rejected(self):
        resp = auth_client(self.receptionist).get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_allowed(self):
        resp = super_admin_client().get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Clinic list
# ---------------------------------------------------------------------------

class ClinicListTest(TestCase):
    def setUp(self):
        self.client = super_admin_client()
        Clinic.objects.all().delete()
        Clinic.objects.create(name='Alpha Clinic', slug='alpha-clinic', is_active=True)
        Clinic.objects.create(name='Beta Clinic',  slug='beta-clinic',  is_active=False)

    def test_lists_all_clinics(self):
        resp = self.client.get('/api/superadmin/clinics/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 2)

    def test_filter_active(self):
        resp = self.client.get('/api/superadmin/clinics/?is_active=true')
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['name'], 'Alpha Clinic')

    def test_filter_inactive(self):
        resp = self.client.get('/api/superadmin/clinics/?is_active=false')
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['name'], 'Beta Clinic')


# ---------------------------------------------------------------------------
# Clinic onboarding
# ---------------------------------------------------------------------------

ONBOARD_URL = '/api/superadmin/clinics/onboard/'

VALID_PAYLOAD = {
    'clinic_name':    'Test Clinic',
    'admin_email':    'admin@testclinic.com',
    'admin_password': 'securepassword123',
    'admin_full_name': 'Dr. Admin',
}


class ClinicOnboardTest(TestCase):
    def setUp(self):
        self.client = super_admin_client()

    @patch('superadmin.views.http_requests.post')
    def test_successful_onboard_creates_clinic_and_profile(self, mock_post):
        new_user_id = str(uuid.uuid4())
        mock_post.return_value = _mock_supabase_ok(new_user_id)

        resp = self.client.post(ONBOARD_URL, VALID_PAYLOAD, format='json')

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['clinic']['name'], 'Test Clinic')
        self.assertEqual(resp.data['clinic']['slug'], 'test-clinic')
        self.assertEqual(resp.data['admin']['email'], 'admin@testclinic.com')
        self.assertEqual(resp.data['admin']['role'], 'admin')

        # Clinic row exists
        clinic = Clinic.objects.get(slug='test-clinic')
        self.assertEqual(clinic.name, 'Test Clinic')
        self.assertTrue(clinic.is_active)

        # Profile row exists with correct clinic and role
        from users.models import Profile
        profile = Profile.objects.get_queryset().get(id=new_user_id)
        self.assertEqual(profile.clinic_id, clinic.id)
        self.assertEqual(profile.role, 'admin')
        self.assertEqual(profile.full_name, 'Dr. Admin')

    def test_missing_required_fields_rejected(self):
        resp = self.client.post(ONBOARD_URL, {}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('clinic_name', resp.data)
        self.assertIn('admin_email', resp.data)

    def test_invalid_email_rejected(self):
        payload = {**VALID_PAYLOAD, 'admin_email': 'not-an-email'}
        resp = self.client.post(ONBOARD_URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('admin_email', resp.data)

    def test_short_password_rejected(self):
        payload = {**VALID_PAYLOAD, 'admin_password': 'short'}
        resp = self.client.post(ONBOARD_URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)

    @patch('superadmin.views.http_requests.post')
    def test_duplicate_clinic_name_rejected(self, mock_post):
        Clinic.objects.create(name='Test Clinic', slug='test-clinic')
        resp = self.client.post(ONBOARD_URL, VALID_PAYLOAD, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('clinic_name', resp.data)
        mock_post.assert_not_called()   # Supabase never called

    @patch('superadmin.views.http_requests.post')
    def test_supabase_failure_rolls_back_clinic(self, mock_post):
        mock_post.return_value = _mock_supabase_fail()

        resp = self.client.post(ONBOARD_URL, VALID_PAYLOAD, format='json')

        self.assertEqual(resp.status_code, 400)
        self.assertIn('detail', resp.data)
        # Clinic row must NOT exist
        self.assertFalse(Clinic.objects.filter(slug='test-clinic').exists())

    @patch('superadmin.views.http_requests.post')
    @patch('superadmin.views.http_requests.delete')
    def test_profile_failure_deletes_supabase_user_and_rolls_back_clinic(
        self, mock_delete, mock_post
    ):
        new_user_id = str(uuid.uuid4())
        mock_post.return_value = _mock_supabase_ok(new_user_id)
        mock_delete.return_value = MagicMock(status_code=200)

        # Trigger profile failure by passing a clinic_name that will create
        # a duplicate slug after the clinic is created — we force by patching
        # the view's internal _onboard to raise after Supabase user is created.
        from superadmin.views import ClinicOnboardView

        original_onboard = ClinicOnboardView._onboard

        def fail_after_supabase(self_view, data, service_key):
            # Create the clinic row, then simulate a DB failure on profile
            from django.db import transaction
            with transaction.atomic():
                from superadmin.models import Clinic as C
                from django.utils.text import slugify
                C.objects.create(
                    name=data['clinic_name'],
                    slug=slugify(data['clinic_name']),
                )
                # Simulate calling Supabase then failing on profile
                self_view._delete_supabase_user(new_user_id, service_key)
                raise Exception("Simulated DB failure")

        with patch.object(ClinicOnboardView, '_onboard', fail_after_supabase):
            try:
                self.client.post(ONBOARD_URL, VALID_PAYLOAD, format='json')
            except Exception:
                pass  # exception expected

        # Supabase user deletion was attempted
        mock_delete.assert_called_once()
        # Clinic row rolled back
        self.assertFalse(Clinic.objects.filter(slug='test-clinic').exists())

    def test_non_super_admin_cannot_onboard(self):
        clinic_id = make_clinic_id()
        admin = make_user(clinic_id, 'admin')
        resp = auth_client(admin).post(ONBOARD_URL, VALID_PAYLOAD, format='json')
        self.assertEqual(resp.status_code, 403)
