"""Pointer fields linking ``muk_ai.provider`` to the LLM connector (FC-4)."""

from odoo import fields, models


class AIProvider(models.Model):
    _inherit = 'muk_ai.provider'

    flight_connector_id = fields.Many2one(
        comodel_name='workflow.connector',
        string='LLM Connector',
        help=(
            'Connector supplying the endpoint and the account whose secrets '
            'authenticate requests. Used by the provider "Workflow Studio '
            'LLM connector".'
        ),
    )
    flight_account_id = fields.Many2one(
        comodel_name='workflow.connector.account',
        string='Connector Account',
        domain="[('connector_id', '=', flight_connector_id)]",
        help='The account whose secrets authenticate. Empty: the connector default account.',
    )
