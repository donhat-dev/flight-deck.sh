import { describe, expect, test } from '@odoo/hoot';
import { animationFrame, click } from '@odoo/hoot-dom';
import {
    mockService,
    mountWithCleanup,
    onRpc,
    patchTranslations,
} from '@web/../tests/web_test_helpers';
import { defineMailModels } from '@mail/../tests/mail_test_helpers';

import { FlightToolsPopover } from '@flight_ai_chat/tools_popover/tools_popover';

describe.current.tags('flight_ai_chat');
defineMailModels();
patchTranslations();

function settings(disabled = []) {
    return {
        groups: [
            {
                key: 'read',
                label: 'Read',
                tools: [
                    { name: 'search_read', description: 'Read records', enabled: !disabled.includes('search_read') },
                    { name: 'list_models', description: 'List models', enabled: !disabled.includes('list_models') },
                ],
            },
            {
                key: 'write',
                label: 'Write',
                tools: [
                    { name: 'create_record', description: 'Create a record', enabled: !disabled.includes('create_record') },
                ],
            },
        ],
        disabled_count: disabled.length,
        server: { label: 'This database (MuK MCP)', tool_count: 3 },
    };
}

function registerMocks(calls) {
    onRpc('muk_ai.session', 'flight_get_tool_settings', () => settings(calls.disabled));
    onRpc('muk_ai.session', 'flight_set_tool_filter', ({ args }) => {
        calls.set.push(args[1]);
        calls.disabled = args[1];
        return settings(args[1]);
    });
    mockService('notification', {
        add: (message, options) => calls.notifications.push({ message, options }),
    });
}

function findGroup(label) {
    return [...document.querySelectorAll('.o_flight_tools_group')].find(
        (el) => el.querySelector('.fw-semibold')?.textContent.trim() === label,
    );
}

function groupLink(group, text) {
    return [...group.querySelectorAll('a')].find(
        (a) => a.textContent.trim() === text,
    );
}

test('FC-1/FC-2: renders groups and toggles a tool', async () => {
    const calls = { disabled: [], set: [], notifications: [] };
    registerMocks(calls);
    await mountWithCleanup(FlightToolsPopover, {
        props: { sessionId: 11, inline: true },
    });
    await animationFrame();
    expect('.o_flight_tool').toHaveCount(3);
    const boxes = document.querySelectorAll('.o_flight_tool input[type=checkbox]');
    expect(boxes[0].checked).toBe(true);
    await click(boxes[0]);
    await animationFrame();
    expect(calls.set).toEqual([['search_read']]);
    const after = document.querySelectorAll('.o_flight_tool input[type=checkbox]');
    expect(after[0].checked).toBe(false);
});

test('FC-1/FC-2: All off disables the whole group', async () => {
    const calls = { disabled: [], set: [], notifications: [] };
    registerMocks(calls);
    await mountWithCleanup(FlightToolsPopover, {
        props: { sessionId: 11, inline: true },
    });
    await animationFrame();
    await click(groupLink(findGroup('Read'), 'All off'));
    await animationFrame();
    expect(calls.set).toEqual([['search_read', 'list_models']]);
});

test('FC-1/FC-2: All on re-enables the group', async () => {
    const calls = {
        disabled: ['search_read', 'list_models'],
        set: [],
        notifications: [],
    };
    registerMocks(calls);
    await mountWithCleanup(FlightToolsPopover, {
        props: { sessionId: 11, inline: true },
    });
    await animationFrame();
    await click(groupLink(findGroup('Read'), 'All on'));
    await animationFrame();
    expect(calls.set).toEqual([[]]);
    const boxes = findGroup('Read').querySelectorAll('input[type=checkbox]');
    expect([...boxes].every((box) => box.checked)).toBe(true);
});
