"""Provider adapter keyed by the Workflow Studio LLM connector (FC-4).

The provider record stores no key: every request resolves the connector
account's ``api_key`` secret through the connector's default auth profile
(slot ``token`` → secret ``api_key``). Connector, account and auth profile
are read with ``sudo()`` — and only these — because an ordinary MuK user
cannot read ``workflow.connector`` rows.
"""

from odoo.exceptions import UserError

from odoo.addons.muk_ai.providers import REGISTRY
from odoo.addons.muk_ai.providers.openai import OpenAIProvider
from odoo.addons.workflow_studio.models.security.secret_broker import SecretBroker


class FlightLLMConnectorProvider(OpenAIProvider):
    """OpenAI-compatible endpoint authenticated by a connector account."""

    name = 'flight_llm_connector'
    label = 'Workflow Studio LLM connector'
    default_model = 'opencode/deepseek-v4.1-flash-free'
    default_url = 'http://host.docker.internal:3002/v1'
    # No region choice: the URL lives on the connector.
    regions = ()
    supports_web_search = False
    supports_code_interpreter = False

    # ----------------------------------------------------------
    # Connector link
    # ----------------------------------------------------------

    def _flight_connector(self):
        """Return the provider's connector (sudo, its record only).

        A connector bound to a different company is out of scope: the chat
        of company A must never reach company B's credentials through sudo.
        """
        connector = self.provider.sudo().flight_connector_id
        if connector.company_id and connector.company_id != self.env.company:
            return connector.browse()
        return connector

    def _flight_account(self):
        """Return the usable account, or an empty recordset when unready.

        The account is the provider's own, else the connector's default;
        it must belong to the connector, be active and hold every required
        secret, and its company must be the caller's or unset.
        """
        provider = self.provider.sudo()
        connector = self._flight_connector()
        empty = self.env['workflow.connector.account'].sudo().browse()
        if not connector or not connector.default_auth_profile_id:
            return empty
        account = provider.flight_account_id or connector.default_account_id
        if (
            not account
            or not account.active
            or account.connector_id != connector
            or account.missing_secrets
            or (
                account.company_id
                and account.company_id != self.env.company
            )
        ):
            return empty
        return account

    # ----------------------------------------------------------
    # Config
    # ----------------------------------------------------------

    @property
    def api_url(self) -> str:
        """The connector's base URL is the one home of the endpoint.

        An empty connector URL refuses loudly: falling back to the default
        endpoint would silently ship the account's credentials and the chat
        payload somewhere the admin did not configure.
        """
        connector = self._flight_connector()
        if not connector:
            return self.default_url
        if not (url := connector.get_effective_base_url()):
            raise UserError(
                self.env._(
                    '%(provider)s: set a Base URL on the %(connector)s connector.',
                    provider=self.label,
                    connector=connector.display_name,
                )
            )
        return url

    @property
    def _api_key(self) -> str:
        """Resolve the bearer token at call time.

        ``''`` is returned only while nothing is configured; once an account
        is ready by metadata, a token that cannot be resolved is a safe
        error, never an empty ``Bearer`` header.
        """
        connector = self._flight_connector()
        account = self._flight_account()
        if not account or not connector:
            return ''
        broker_key = (
            connector.default_auth_profile_id.get_secret_refs() or {}
        ).get('token')
        token = ''
        if broker_key:
            broker = SecretBroker(
                self.env,
                mask_mode=False,
                company=connector.company_id,
                connector=connector,
                account=account,
            )
            try:
                token = broker.get(broker_key) or ''
            except ValueError:
                token = ''
        if not token:
            raise UserError(
                self.env._(
                    '%(provider)s could not read the API key from the '
                    'connector account "%(account)s". Check its secrets.',
                    provider=self.label,
                    account=account.display_name,
                )
            )
        return token

    @property
    def authenticated(self) -> bool:
        """Report readiness from metadata only; never reads the secret."""
        return bool(self._flight_account())


REGISTRY[FlightLLMConnectorProvider.name] = FlightLLMConnectorProvider
