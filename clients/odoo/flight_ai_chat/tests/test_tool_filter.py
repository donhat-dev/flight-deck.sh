"""FC-1/FC-2: per-session tool switches on muk_ai.session."""

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.muk_ai.tests.common import AITestCommon


@tagged('post_install', '-at_install')
class TestFlightToolFilter(AITestCommon):
    """FC-1/FC-2: per-chat tool switches list, hide and block tools."""

    def _session(self):
        return self.env['muk_ai.session'].create({'name': 'tool switches'})

    def _any_tool_name(self, session):
        settings = session.flight_get_tool_settings()
        return settings['groups'][0]['tools'][0]['name']

    def test_get_settings_lists_the_catalog(self):
        """FC-1: the settings list every tool, enabled, with a server line."""
        session = self._session()
        settings = session.flight_get_tool_settings()
        self.assertTrue(settings['groups'])
        self.assertEqual(settings['disabled_count'], 0)
        tool = settings['groups'][0]['tools'][0]
        self.assertTrue(tool['enabled'])
        self.assertIn('name', tool)
        self.assertIn('description', tool)
        self.assertTrue(settings['server']['label'])
        self.assertGreaterEqual(settings['server']['tool_count'], 1)

    def test_set_filter_hides_and_blocks_the_tool(self):
        """FC-1: a switched-off tool leaves the catalog and cannot run."""
        session = self._session()
        name = self._any_tool_name(session)
        settings = session.flight_set_tool_filter([name])
        self.assertEqual(settings['disabled_count'], 1)
        entry = next(
            tool
            for group in settings['groups']
            for tool in group['tools']
            if tool['name'] == name
        )
        self.assertFalse(entry['enabled'])
        catalog_names = {e['name'] for e in session._get_filtered_catalog()}
        self.assertNotIn(name, catalog_names)
        schema_names = {s['name'] for s in session._get_tool_schema()}
        self.assertNotIn(name, schema_names)
        # The unfiltered view still shows it for the popup.
        self.assertIn(
            name,
            {e['name'] for e in session._flight_unfiltered_catalog()},
        )
        # Turning it back on restores the catalog.
        session.flight_set_tool_filter([])
        self.assertIn(
            name,
            {e['name'] for e in session._get_filtered_catalog()},
        )

    def test_disabled_tool_is_never_executed(self):
        """FC-1, B1/S5: a switched-off tool never reaches the executor."""
        session = self._session()
        name = self._any_tool_name(session)
        patcher, calls = self._patch_tool({name: '{"ok": true}'})
        with patcher:
            # Enabled: the executor runs and succeeds.
            result, ok = session._dispatch_tool_call(name, {}, 'call_1')
            self.assertTrue(ok)
            self.assertEqual(calls, [name])
            session.flight_set_tool_filter([name])
            # A stale schema call is refused before dispatch.
            result, ok = session._dispatch_tool_call(name, {}, 'call_2')
            self.assertFalse(ok)
            # Inline load+call: the name no longer resolves at all.
            response = session._dispatch_tool_load(
                {'names': [name], 'call': {'name': name, 'arguments': {}}}
            )
            self.assertIn(name, response['unknown'])
            # Approval resume of a call emitted before the switch.
            session._resume_tool_round(
                {
                    'call_id': 'call_3',
                    'name': name,
                    'arguments': {},
                    'outputs': [],
                    'tool_calls': [],
                    'resume_index': -1,
                    'has_terminating': False,
                },
                approved=True,
            )
            self.assertEqual(calls, [name])

    def test_write_cannot_clear_the_deny_list_while_busy(self):
        """FC-1, S2: a generic write cannot empty the list mid-turn."""
        session = self._session()
        name = self._any_tool_name(session)
        session.flight_set_tool_filter([name])
        session.write({'state': 'running'})
        with self.assertRaises(UserError):
            session.write({'flight_disabled_tool_names': False})
        with self.assertRaises(UserError):
            session.sudo().write({'flight_disabled_tool_names': []})
        self.assertEqual(session.flight_disabled_tool_names, [name])
        session.write({'state': 'done'})
        session.write({'flight_disabled_tool_names': False})
        self.assertFalse(session.flight_disabled_tool_names)

    def test_unknown_names_are_refused(self):
        """FC-1: a name that is not in the catalogue raises."""
        session = self._session()
        with self.assertRaises(UserError):
            session.flight_set_tool_filter(['not_a_tool_xyz'])

    def test_filter_is_refused_while_busy(self):
        """FC-1, B4: running/waiting/compacting sessions refuse changes."""
        session = self._session()
        name = self._any_tool_name(session)
        for state in ('running', 'waiting', 'compacting'):
            session.write({'state': state})
            with self.assertRaises(UserError):
                session.flight_set_tool_filter([name])
        session.write({'state': 'new'})
        session.flight_set_tool_filter([name])

    def test_other_user_cannot_read_or_change(self):
        """FC-1, B3: the owner check follows MuK's record rules."""
        session = self._session()
        other = new_test_user(self.env, login='flight_other')
        with self.assertRaises(AccessError):
            session.with_user(other).flight_get_tool_settings()
        with self.assertRaises(AccessError):
            session.with_user(other).flight_set_tool_filter([])
