"""FC-4: the provider on the LLM connector reads its key from the connector account."""

import os
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.muk_ai.tests.common import AITestCommon
from odoo.addons.workflow_studio.models.security import secret_keys

FAKE_KEY = 'fake-llm-key-000'


@tagged('post_install', '-at_install')
class TestFlightProvider(AITestCommon):
    """FC-4: url from the connector, bearer from the account secret."""

    def setUp(self):
        super().setUp()
        key_patch = patch.dict(
            os.environ, {secret_keys.ENV_VAR: Fernet.generate_key().decode()}
        )
        key_patch.start()
        self.addCleanup(key_patch.stop)
        self.connector = self.env.ref('workflow_connector_llm.connector_llm')
        self.account = self.env['workflow.connector.account'].create(
            {'connector_id': self.connector.id, 'name': 'Chat test'}
        )
        self.account._set_secret('api_key', FAKE_KEY)
        # The shipped record (unique(name, code)) is reused, not created.
        self.provider = self.env.ref('flight_ai_chat.provider_llm_connector')
        self.provider.flight_connector_id = self.connector
        self.provider.flight_account_id = False
        self.client = self.provider._get_client()

    def test_url_comes_from_the_connector(self):
        """FC-4: the connector base URL is the endpoint."""
        self.assertEqual(self.client.api_url, 'http://host.docker.internal:3002/v1')

    def test_headers_carry_the_account_secret(self):
        """FC-4: Authorization bears the account's api_key, nothing stored."""
        self.assertTrue(self.client.authenticated)
        self.assertEqual(
            self.client.headers()['Authorization'],
            f'Bearer {FAKE_KEY}',
        )
        self.assertFalse(self.provider.sudo().api_key)

    def test_account_missing_secrets_is_not_authenticated(self):
        """FC-4: an account without the secret cannot serve."""
        empty = self.env['workflow.connector.account'].create(
            {'connector_id': self.connector.id, 'name': 'Empty'}
        )
        self.provider.flight_account_id = empty
        self.assertFalse(self.client.authenticated)
        with self.assertRaises(UserError):
            self.client.api_key  # noqa: B018 — the property raises

    def test_default_account_is_used_when_none_is_pinned(self):
        """FC-4: no account field means the connector's default account."""
        self.assertEqual(self.client._api_key, FAKE_KEY)

    def test_inactive_account_is_not_authenticated(self):
        """FC-4: an archived account sends nothing."""
        self.account.active = False
        self.assertFalse(self.client.authenticated)

    def test_foreign_company_connector_cannot_serve(self):
        """FC-4, S1: another company's credentials are out of scope."""
        other_company = self.env['res.company'].create({'name': 'Other Co'})
        self.connector.sudo().company_id = other_company
        # The caller's company — admin or an ordinary MuK user — decides.
        self.assertFalse(self.client.authenticated)
        user = new_test_user(
            self.env, login='flight_muk_user', groups='base.group_user'
        )
        user_client = self.provider.with_user(user)._get_client()
        self.assertFalse(user_client.authenticated)
        with self.assertRaises(UserError):
            user_client.api_key  # noqa: B018 — the property raises

    def test_unreadable_secret_is_a_safe_error(self):
        """FC-4, S3: an unreadable credential raises, never 'Bearer '."""
        with patch.dict(
            os.environ,
            {secret_keys.ENV_VAR: Fernet.generate_key().decode()},
        ):
            # Metadata still reads ready; decryption now fails.
            self.assertTrue(self.client.authenticated)
            with self.assertRaises(UserError):
                self.client._api_key  # noqa: B018 — the property raises
            with self.assertRaises(UserError):
                self.client.headers()

    def test_non_default_url_is_used(self):
        """FC-4, S4: the connector's own URL wins over the default."""
        self.connector.sudo().base_url = 'http://llm.internal:9000/v1/'
        self.assertEqual(self.client.api_url, 'http://llm.internal:9000/v1')

    def test_empty_connector_url_refuses(self):
        """FC-4, S4: no silent fallback when the URL is cleared."""
        self.connector.sudo().base_url = False
        with self.assertRaises(UserError):
            self.client.api_url  # noqa: B018 — the property raises
