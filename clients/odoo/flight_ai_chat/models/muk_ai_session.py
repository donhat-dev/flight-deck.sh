"""Per-session tool switches on ``muk_ai.session`` (FC-1, FC-2).

``flight_disabled_tool_names`` is a deny list: tools the agent gains later
stay on. The list is enforced in two places, because hiding a tool from the
model is not enough on its own:

- ``_get_filtered_catalog`` drops the names, so the schema list, the
  ``<available_tools>`` prompt block and ``tool_load`` never offer them
  (MuK funnels all three through that one method).
- ``_dispatch_tool_call`` refuses a disabled name that is called anyway —
  a schema loaded before the switch, a call emitted by the model, or an
  approval resume — with the same ``({'error': ...}, False)`` result shape
  MuK returns for a tool failure.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.muk_mcp.core.tool import get_tool_index

# States in which a turn is on its way: changing the tool list mid-answer
# would apply silently only to the next turn, so the popup refuses instead.
BUSY_STATES = ('running', 'waiting', 'compacting')


class AISession(models.Model):
    _inherit = 'muk_ai.session'

    flight_disabled_tool_names = fields.Json(
        string='Disabled Tools',
        readonly=True,
        help=(
            'Tool names turned off for this chat. A deny list: tools the '
            'agent gains later stay on. Empty keeps the agent tool set.'
        ),
    )

    # ----------------------------------------------------------
    # Helper Tools
    # ----------------------------------------------------------

    def _flight_disabled_tool_set(self) -> set:
        """Return the disabled tool names as a set of strings."""
        return {
            name
            for name in (self.flight_disabled_tool_names or [])
            if isinstance(name, str)
        }

    def _get_filtered_catalog(self) -> list[dict]:
        """Drop the tools the user turned off for this chat."""
        catalog = super()._get_filtered_catalog()
        disabled = self._flight_disabled_tool_set()
        if not disabled:
            return catalog
        return [
            entry for entry in catalog if entry.get('name') not in disabled
        ]

    def _flight_unfiltered_catalog(self) -> list[dict]:
        """Return the catalogue without this session's deny list.

        Used by the tool-switch list, which must keep turned-off tools
        visible so they can be switched back on. Deliberately not a context
        flag: the worker restores the RPC context from ``user_context``, and
        a key read from ``env.context`` would leak there and silently
        re-enable the denied tools mid-turn.
        """
        return super()._get_filtered_catalog()

    def _dispatch_tool_call(self, name: str, arguments: dict, call_id: str) -> tuple:
        """Refuse disabled tools even when a call for one is emitted anyway."""
        disabled = self._flight_disabled_tool_set()
        if name in disabled or (
            '.' in name and name.rpartition('.')[2] in disabled
        ):
            return (
                {
                    'error': (
                        f'Tool {name} is turned off for this chat. Do not '
                        'call it again.'
                    )
                },
                False,
            )
        return super()._dispatch_tool_call(name, arguments, call_id)

    def write(self, vals: dict) -> bool:
        """Hold the deny list for the whole turn on every write path.

        ``muk_mcp`` record tools reach ``write`` directly and could clear
        the list mid-answer, so the busy check lives here, not only in the
        RPC. Written values must stay a list of names (or ``False``).
        """
        if 'flight_disabled_tool_names' in vals:
            if self.filtered(lambda session: session.state in BUSY_STATES):
                raise UserError(
                    _(
                        'Tools cannot be changed while the session is busy. '
                        'Wait for the answer to finish.'
                    )
                )
            value = vals['flight_disabled_tool_names']
            if value and not (
                isinstance(value, (list, tuple))
                and all(isinstance(name, str) for name in value)
            ):
                raise UserError(
                    _('Disabled tools must be a list of tool names.')
                )
        return super().write(vals)

    # ----------------------------------------------------------
    # RPC
    # ----------------------------------------------------------

    def flight_get_tool_settings(self) -> dict:
        """Return the tool switches of this chat for the popup/Session tab.

        Turned-off tools stay listed (``enabled: False``) so they can be
        turned back on. Reading is allowed while a turn runs.
        """
        self.ensure_one()
        self.check_access('read')
        disabled = self._flight_disabled_tool_set()
        index = get_tool_index(self.env, registry='odoo')
        labels = {
            'read': _('Read'),
            'write': _('Write'),
        }
        groups = {}
        for entry in self._flight_unfiltered_catalog():
            name = entry.get('name')
            if not name:
                continue
            category = (index.get(name) or {}).get('category') or 'other'
            groups.setdefault(category, []).append(
                {
                    'name': name,
                    'description': entry.get('description') or '',
                    'enabled': name not in disabled,
                }
            )
        ordered = []
        for key, tools in sorted(groups.items()):
            ordered.append(
                {
                    'key': key,
                    'label': labels.get(key) or _('Other'),
                    'tools': sorted(tools, key=lambda t: t['name']),
                }
            )
        shown_names = {t['name'] for g in ordered for t in g['tools']}
        return {
            'groups': ordered,
            'disabled_count': len(disabled & shown_names),
            'server': {
                'label': _('This database (MuK MCP)'),
                'tool_count': len(shown_names),
            },
        }

    def flight_set_tool_filter(self, disabled_names) -> dict:
        """Turn tools off for this chat, or back on.

        Unknown names raise instead of being stored: a typo would otherwise
        look switched off while doing nothing. The next turn reads the new
        list; a turn already on its way keeps the one it started with, so
        the change is refused while the session is busy.

        :raise UserError: on a busy session or a name not in the catalogue
        """
        self.ensure_one()
        self.check_access('write')
        if self.state in BUSY_STATES:
            raise UserError(
                _(
                    'Tools cannot be changed while the session is %(state)s. '
                    'Wait for the answer to finish.',
                    state=self.state,
                )
            )
        if not isinstance(disabled_names, (list, tuple)):
            raise UserError(_('disabled_names must be a list of tool names.'))
        names = {
            str(name).strip()
            for name in disabled_names
            if isinstance(name, (str, int)) and str(name).strip()
        }
        known = {
            entry.get('name') for entry in self._flight_unfiltered_catalog()
        }
        if unknown := sorted(names - known):
            raise UserError(
                _('Unknown tools for this chat: %(names)s.', names=', '.join(unknown))
            )
        self.write({'flight_disabled_tool_names': sorted(names) or False})
        self._publish_event('state', {'state': self.state})
        return self.flight_get_tool_settings()
