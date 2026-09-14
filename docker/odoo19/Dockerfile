# odoo:19.0 plus the Claude Code binary and fd-runner, so the harness runs inside
# the Odoo runtime. Build context is the repository root (see compose.yml).
# Credentials are never baked in: the operator signs in once by hand and they land in
# CLAUDE_CONFIG_DIR, which sits in the odoo-data volume.
FROM golang:1.26-alpine AS runner
WORKDIR /src
COPY runner/ .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/fd-runner .

FROM odoo:19.0

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# The native installer drops a per-version binary under ~/.local/share and a symlink
# in ~/.local/bin; copy the resolved binary so it works for any user.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && install -m 0755 "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
    && rm -rf /root/.local/share/claude /root/.local/bin/claude \
    && claude --version

COPY --from=runner /out/fd-runner /usr/local/bin/fd-runner

ENV CLAUDE_CONFIG_DIR=/var/lib/odoo/.claude

USER odoo
