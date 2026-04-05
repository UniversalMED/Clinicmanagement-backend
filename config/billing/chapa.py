"""
Chapa API client.

Thin wrapper around the Chapa REST API.
All methods raise ChapaError on failure so callers don't need to inspect
raw HTTP responses.
"""
import hashlib
import hmac
import logging

import requests

logger = logging.getLogger(__name__)

CHAPA_BASE_URL = 'https://api.chapa.co/v1'


class ChapaError(Exception):
    pass


class ChapaClient:
    def __init__(self, secret_key: str):
        self._secret_key = secret_key
        self._headers = {
            'Authorization': f'Bearer {secret_key}',
            'Content-Type': 'application/json',
        }

    def initialize(self, *, tx_ref: str, amount: str, currency: str = 'ETB',
                   email: str = '', callback_url: str = '', return_url: str = '',
                   first_name: str = '', last_name: str = '', description: str = '') -> str:
        """
        Initialize a Chapa payment.

        Returns:
            checkout_url — redirect the patient/payer here.

        Raises:
            ChapaError on failure.
        """
        payload = {
            'tx_ref':       tx_ref,
            'amount':       str(amount),
            'currency':     currency,
            'email':        email,
            'first_name':   first_name,
            'last_name':    last_name,
            'callback_url': callback_url,
            'return_url':   return_url,
            'customization': {
                'title':       'Clinic Payment',
                'description': description or f'Invoice payment — ref {tx_ref}',
            },
        }
        try:
            resp = requests.post(
                f'{CHAPA_BASE_URL}/transaction/initialize',
                json=payload,
                headers=self._headers,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ChapaError(f'Network error contacting Chapa: {exc}') from exc

        if resp.status_code != 200:
            msg = _extract_error(resp)
            raise ChapaError(f'Chapa initialization failed: {msg}')

        data = resp.json().get('data', {})
        checkout_url = data.get('checkout_url')
        if not checkout_url:
            raise ChapaError('Chapa returned no checkout_url.')
        return checkout_url

    def verify(self, tx_ref: str) -> dict:
        """
        Verify a transaction by tx_ref.

        Returns:
            dict with keys: status, amount, currency, tx_ref, mode, reference.

        Raises:
            ChapaError on failure.
        """
        try:
            resp = requests.get(
                f'{CHAPA_BASE_URL}/transaction/verify/{tx_ref}',
                headers=self._headers,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ChapaError(f'Network error verifying Chapa transaction: {exc}') from exc

        if resp.status_code != 200:
            msg = _extract_error(resp)
            raise ChapaError(f'Chapa verification failed: {msg}')

        return resp.json().get('data', {})

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """
        Verify the HMAC-SHA256 signature Chapa attaches to webhook requests.
        Chapa signs the raw body with your secret key.
        """
        if not signature:
            return False
        expected = hmac.new(
            self._secret_key.encode('utf-8'),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


def _extract_error(resp) -> str:
    try:
        body = resp.json()
        return body.get('message') or body.get('msg') or resp.text
    except Exception:
        return resp.text
