{
    "name": "FlightDeck backend look, light",
    "summary": "A white icon rail that carries the whole navigation, and no top bar",
    "version": "19.0.1.0.0",
    "author": "NAKIVO",
    "license": "LGPL-3",
    "category": "Themes/Backend",
    # Everything the rail can already do — the two toggles, the app menu and
    # the systray moving into it — comes from the module below. This one turns
    # the surfaces white, moves the systray to the head of the rail, puts each
    # app's sections under the app itself, and starts narrow with the bar off.
    # mail, for the systray labels: the unread and activity counts are written
    # into those components' own templates, so the label and the badge can only
    # ever read the same number.
    "depends": ["flightdeck_theme", "mail"],
    "assets": {
        "web._assets_primary_variables": [
            (
                "before",
                "web/static/src/scss/primary_variables.scss",
                "flightdeck_theme_light/static/src/scss/variables_early.scss",
            ),
            "flightdeck_theme_light/static/src/scss/variables.scss",
        ],
        "web.assets_backend": [
            "flightdeck_theme_light/static/src/webclient/**/*.js",
            "flightdeck_theme_light/static/src/webclient/**/*.xml",
            "flightdeck_theme_light/static/src/webclient/**/*.scss",
        ],
    },
    "post_init_hook": "collapse_sidebars",
    "installable": True,
    "application": False,
    "auto_install": False,
}
