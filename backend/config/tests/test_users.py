"""
User management integration tests.

Covers:
  - Role-based access control (admin-only endpoints)
  - Admin can assign / update roles
  - Non-admin is blocked from admin endpoints
  - Clinic isolation: admin from clinic A cannot read or modify clinic B users
  - Users can update their own profile; cannot update others
"""
import uuid

from django.test import TestCase

from users.models import Profile
from .utils import auth_client, make_user


class UserListTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin', 'Admin User')
        self.doctor = make_user(self.clinic_id, 'doctor', 'Doctor User')
        self.receptionist = make_user(self.clinic_id, 'receptionist')

    def test_admin_can_list_clinic_users(self):
        resp = auth_client(self.admin).get('/api/users/')
        self.assertEqual(resp.status_code, 200)
        ids = [u['id'] for u in resp.data['results']]
        self.assertIn(str(self.doctor.id), ids)
        self.assertIn(str(self.receptionist.id), ids)

    def test_doctor_cannot_list_users(self):
        resp = auth_client(self.doctor).get('/api/users/')
        self.assertEqual(resp.status_code, 403)

    def test_receptionist_cannot_list_users(self):
        resp = auth_client(self.receptionist).get('/api/users/')
        self.assertEqual(resp.status_code, 403)

    def test_admin_list_filtered_by_role(self):
        resp = auth_client(self.admin).get('/api/users/?role=doctor')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(u['role'] == 'doctor' for u in resp.data['results']))

    def test_clinic_isolation_admin_cannot_see_other_clinic_users(self):
        other_clinic_id = uuid.uuid4()
        other_user = make_user(other_clinic_id, 'doctor', 'Other Clinic Doctor')

        resp = auth_client(self.admin).get('/api/users/')
        ids = [u['id'] for u in resp.data['results']]
        self.assertNotIn(str(other_user.id), ids)


class AssignRoleTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')

    def test_admin_can_assign_role(self):
        resp = auth_client(self.admin).patch(
            f'/api/users/{self.doctor.id}/role/',
            {'role': 'lab_tech'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.role, 'lab_tech')

    def test_doctor_cannot_assign_role(self):
        resp = auth_client(self.doctor).patch(
            f'/api/users/{self.admin.id}/role/',
            {'role': 'receptionist'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_invalid_role_value_returns_400(self):
        resp = auth_client(self.admin).patch(
            f'/api/users/{self.doctor.id}/role/',
            {'role': 'god_mode'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_clinic_isolation_admin_cannot_assign_role_in_other_clinic(self):
        other_clinic_id = uuid.uuid4()
        other_user = make_user(other_clinic_id, 'doctor')

        resp = auth_client(self.admin).patch(
            f'/api/users/{other_user.id}/role/',
            {'role': 'admin'},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)


class UpdateProfileTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor', 'Dr. Smith')
        self.other_doctor = make_user(self.clinic_id, 'doctor', 'Dr. Jones')

    def test_user_can_update_own_profile(self):
        resp = auth_client(self.doctor).patch(
            f'/api/users/{self.doctor.id}/',
            {'full_name': 'Dr. Updated'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.full_name, 'Dr. Updated')

    def test_user_cannot_update_other_users_profile(self):
        resp = auth_client(self.doctor).patch(
            f'/api/users/{self.other_doctor.id}/',
            {'full_name': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_update_any_user_in_clinic(self):
        resp = auth_client(self.admin).patch(
            f'/api/users/{self.doctor.id}/',
            {'full_name': 'Admin Updated'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_blank_full_name_returns_400(self):
        resp = auth_client(self.doctor).patch(
            f'/api/users/{self.doctor.id}/',
            {'full_name': '   '},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


class CurrentUserTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin', 'Admin User')

    def test_me_returns_own_profile(self):
        resp = auth_client(self.admin).get('/api/users/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], str(self.admin.id))
        self.assertEqual(resp.data['role'], 'admin')
