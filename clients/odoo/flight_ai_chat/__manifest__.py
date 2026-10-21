{
    'name': 'Flight AI Chat',
    'summary': 'MuK AI chat on Flightdoo: per-chat tools, editor context, LLM connector key',
    'description': """
        Extends MuK AI for Flightdoo: per-chat tool switches (popup header and
        Session tab), the Workflow Studio editor as screen context, and a
        provider on the Workflow Studio LLM connector that reads its key from
        the connector account.
    """,
    'version': '19.0.1.1.0',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'author': 'Nathan Do',
    'depends': [
        'muk_ai',
        'workflow_studio',
        'workflow_connector_llm',
    ],
    'data': [
        'data/provider.xml',
        'views/muk_ai_provider_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'flight_ai_chat/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'flight_ai_chat/static/tests/**/*.test.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
