"""FC-3: the `client_action` view-context kind."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.muk_ai.tests.common import AITestCommon
from odoo.addons.muk_ai.tools.context import format_ui_ctx_tag


@tagged('post_install', '-at_install')
class TestFlightViewContext(AITestCommon):
    """FC-3: the editor pins a client-action context the assistant reads."""

    PAYLOAD = {
        'kind': 'client_action',
        'tag': 'workflow_studio.editor_app',
        'title': 'Invoice approval',
        'model': 'ir.workflow',
        'id': 5,
        'selected_steps': [
            {'id': 'n_3', 'type': 'mail_send', 'label': 'Send mail'},
        ],
    }

    def _session(self):
        return self.env['muk_ai.session'].create({'name': 'ctx'})

    def test_client_action_payload_is_cleaned_and_stored(self):
        """FC-3: unknown keys drop, the declared keys persist."""
        session = self._session()
        payload = {**self.PAYLOAD, 'junk': 'dropped', 'evil': '<script>'}
        session.set_view_context(payload)
        ctx = session.view_context
        self.assertNotIn('junk', ctx)
        self.assertNotIn('evil', ctx)
        self.assertEqual(ctx['kind'], 'client_action')
        self.assertEqual(ctx['tag'], 'workflow_studio.editor_app')
        self.assertEqual(ctx['title'], 'Invoice approval')
        self.assertEqual(ctx['model'], 'ir.workflow')
        self.assertEqual(ctx['id'], 5)
        self.assertEqual(ctx['selected_steps'][0]['label'], 'Send mail')

    def test_ui_ctx_tag_renders_the_screen(self):
        """FC-3: the prompt tag names the flow and the selected step."""
        session = self._session()
        session.set_view_context(dict(self.PAYLOAD))
        tag = format_ui_ctx_tag(session.view_context)
        self.assertIn('Invoice approval', tag)
        self.assertIn('Send mail', tag)
        self.assertIn('ir.workflow/5', tag)

    def test_bad_payloads_raise(self):
        """FC-3, B7: tag is a dotted tag, not a single identifier."""
        session = self._session()
        for bad in (
            {**self.PAYLOAD, 'tag': 'not a tag!'},
            {**self.PAYLOAD, 'tag': 'no-dashes'},
            {k: v for k, v in self.PAYLOAD.items() if k != 'tag'},
            {**self.PAYLOAD, 'id': -1},
            {**self.PAYLOAD, 'id': '5'},
            {**self.PAYLOAD, 'selected_steps': 'n_3'},
            {**self.PAYLOAD, 'selected_steps': ['n_3']},
            {**self.PAYLOAD, 'title': 7},
        ):
            with self.assertRaises(UserError, msg=bad):
                session.set_view_context(bad)
        self.assertFalse(session.view_context)

    def test_dotted_tag_is_accepted(self):
        """FC-3, B7: `workflow_studio.editor_app` is valid (per-segment check)."""
        session = self._session()
        session.set_view_context(dict(self.PAYLOAD))
        self.assertEqual(
            session.view_context['tag'], 'workflow_studio.editor_app'
        )
        for tag in ('x', 'a.b.c', '_private.x'):
            session.set_view_context({**self.PAYLOAD, 'tag': tag})
            self.assertEqual(session.view_context['tag'], tag)
        for tag in ('a..b', '.a', 'a.', 'no-dashes'):
            with self.assertRaises(UserError):
                session.set_view_context({**self.PAYLOAD, 'tag': tag})
