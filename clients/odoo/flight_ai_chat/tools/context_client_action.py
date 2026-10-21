"""View-context kind ``client_action``: the screen a client action shows (FC-3).

Registers a cleaner and a segment builder into MuK's registries at import;
both are read at call time by ``clean_view_context_payload`` and
``_format_ctx_tag``, so the kind works without touching MuK files.
"""

from odoo.exceptions import UserError
from odoo.tools.translate import LazyTranslate

from odoo.addons.muk_ai.tools.context import (
    _CLEANERS,
    _SEGMENT_BUILDERS,
    _base_payload,
)

_lt = LazyTranslate('flight_ai_chat')

_STEP_LIMITS = (('id', 64), ('type', 64), ('label', 120))


def _clean_step(step) -> dict:
    """Validate one ``selected_steps`` entry, keeping the declared keys."""
    if not isinstance(step, dict):
        raise UserError(
            _lt('view_context.selected_steps entries must be objects.')
        )
    cleaned = {}
    for key, limit in _STEP_LIMITS:
        value = step.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise UserError(
                _lt('view_context.selected_steps.%s must be a string.', key)
            )
        cleaned[key] = value[:limit]
    return cleaned


def _clean_client_action(payload) -> dict:
    """Validate and normalize a client-action view-context payload.

    ``tag`` is a dotted action tag (``workflow_studio.editor_app``): each
    segment must be an identifier, so the tag itself is not one.

    :raise UserError: on a missing/malformed tag or malformed fields.
    """
    cleaned = _base_payload(payload, 'client_action')
    tag = payload.get('tag')
    if (
        not isinstance(tag, str)
        or not tag
        or any(not part.isidentifier() for part in tag.split('.'))
    ):
        raise UserError(
            _lt('view_context.tag must be a dotted action tag.')
        )
    cleaned['tag'] = tag
    title = payload.get('title')
    if title is not None:
        if not isinstance(title, str):
            raise UserError(_lt('view_context.title must be a string.'))
        cleaned['title'] = title[:200]
    context_id = payload.get('id')
    if context_id is not None:
        if not isinstance(context_id, int) or context_id <= 0:
            raise UserError(
                _lt('view_context.id must be a positive integer.')
            )
        cleaned['id'] = context_id
    steps = payload.get('selected_steps')
    if steps is not None:
        if not isinstance(steps, list):
            raise UserError(
                _lt('view_context.selected_steps must be a list.')
            )
        cleaned['selected_steps'] = [_clean_step(s) for s in steps[:5]]
    return cleaned


def _client_action_segments(p) -> list[str]:
    """Build the ``<ui_ctx>`` segments for a client-action payload."""
    segments = [p.get('title') or p.get('tag') or 'client action']
    if model := p.get('model'):
        segments.append(f'{model}/{p["id"]}' if p.get('id') else model)
    if steps := p.get('selected_steps'):
        labels = [s.get('label') or s.get('id') or '?' for s in steps]
        segments.append(f"selected={', '.join(labels)}")
    return segments


_CLEANERS['client_action'] = _clean_client_action
_SEGMENT_BUILDERS['client_action'] = _client_action_segments
