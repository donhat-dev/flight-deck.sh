{
    'name': 'Flight AI Chat',
    'summary': 'MuK AI chat on Flightdoo: per-chat tools and the editor as context',
    'description': """
        Extends MuK AI for Flightdoo: per-chat tool switches (popup header and
        Session tab) and the Workflow Studio editor as screen context. Chat
        through the AI harness is harness_muk_ai's provider.
    """,
    'version': '19.0.1.2.0',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'author': 'Nathan Do',
    'depends': [
        'muk_ai',
        'workflow_studio',
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
