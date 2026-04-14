"""
Auth middleware integration tests.

These tests send real HTTP requests with encoded JWTs (no force_authenticate)
to verify that SupabaseJWTAuthentication correctly accepts and rejects tokens.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .utils import TEST_JWT_SECRET, make_token, make_user


@override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET, SUPABASE_URL='')
class JWTAuthTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.user = make_user(self.clinic_id, 'doctor')
        self.client = APIClient()

    # --- rejection cases ---

    def test_no_token_returns_401(self):
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_garbage_token_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not.a.jwt')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_wrong_secret_returns_401(self):
        token = make_token(self.user.id, self.clinic_id, 'doctor', secret='wrong-secret')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_expired_token_returns_401(self):
        token = make_token(self.user.id, self.clinic_id, 'doctor', expired=True)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_missing_clinic_id_claim_returns_401(self):
        payload = {
            'sub': str(self.user.id),
            'user_role': 'doctor',
            'aud': 'authenticated',
            'exp': datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm='HS256')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_missing_user_role_claim_returns_401(self):
        payload = {
            'sub': str(self.user.id),
            'clinic_id': str(self.clinic_id),
            'aud': 'authenticated',
            'exp': datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm='HS256')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_unknown_role_returns_401(self):
        token = make_token(self.user.id, self.clinic_id, 'superuser')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    def test_malformed_clinic_id_uuid_returns_401(self):
        token = make_token(self.user.id, self.clinic_id, 'doctor',
                           extra_claims={'clinic_id': 'not-a-uuid'})
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 401)

    # --- acceptance case ---

    def test_valid_token_returns_200(self):
        token = make_token(self.user.id, self.clinic_id, 'doctor')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get('/api/users/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['role'], 'doctor')

