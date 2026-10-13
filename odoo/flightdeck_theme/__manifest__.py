{
    "name": "FlightDeck backend look",
    "summary": "A collapsible app rail and an Enterprise-like palette on top of the MuK theme",
    "version": "19.0.1.0.0",
    "author": "NAKIVO",
    "license": "LGPL-3",
    "category": "Themes/Backend",
    # The rail, the sidebar_type field and the web client grid all come from
    # MuK. Nothing here replaces them; this changes how the rail looks and adds
    # the controls it has no place for.
    "depends": ["muk_web_theme"],
    "assets": {
        "web._assets_primary_variables": [
            # Before the file it overrides, not after it: every variable in
            # this bundle carries !default, so the first assignment is the one
            # that stands. web_enterprise anchors its own palette the same way.
            (
                "before",
                "web/static/src/scss/primary_variables.scss",
                "flightdeck_theme/static/src/scss/primary_variables.scss",
            ),
            # Appended, not anchored: these override values this bundle
            # derives, so they have to be assigned after it has derived them.
            "flightdeck_theme/static/src/scss/navbar_variables.scss",
        ],
        "web._assets_backend_helpers": [
            (
                "prepend",
                "flightdeck_theme/static/src/scss/bootstrap_overridden.scss",
            ),
        ],
        "web.assets_backend": [
            "flightdeck_theme/static/src/webclient/**/*.js",
            "flightdeck_theme/static/src/webclient/**/*.xml",
            "flightdeck_theme/static/src/webclient/**/*.scss",
        ],
    },
    "post_init_hook": "apply_colors",
    "installable": True,
    "application": False,
    "auto_install": False,
}
