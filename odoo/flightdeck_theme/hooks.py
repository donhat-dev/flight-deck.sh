COLOR_ASSET = "/muk_web_colors/static/src/scss/colors_light.scss"
COLOR_BUNDLE = "web._assets_primary_variables"

# The accent and the two status colours, written through MuK's own colour
# editor rather than assigned in SCSS. MuK assigns $o-brand-primary from these
# without !default and prepends the file to the variables bundle, so a later
# assignment either loses or wins by breaking the settings page that edits them.
# Writing them here leaves that page the one authority, with its Reset intact.
# color_brand is the dark surface: Odoo reads it as $o-brand-odoo and paints
# the top bar with it, and rail.scss paints the rail from the same token. One
# value moves both, so the two can never end up a shade apart.
COLORS = [
    {"name": "color_brand", "value": "#111827"},
    {"name": "color_primary", "value": "#1668e3"},
    {"name": "color_warning", "value": "#e99d00"},
    {"name": "color_danger", "value": "#d44c59"},
]


def apply_colors(env):
    env["muk_web_colors.color_assets_editor"].replace_color_variables_values(
        COLOR_ASSET, COLOR_BUNDLE, COLORS,
    )
