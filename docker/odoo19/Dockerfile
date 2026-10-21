# odoo:19.0 plus the Claude Code binary and fd-runner, so the harness runs inside
# the Odoo runtime. Build context is the repository root (see compose.yml).
# Credentials are never baked in: the operator signs in once by hand and they land in
# CLAUDE_CONFIG_DIR, which sits in the odoo-data volume.
FROM golang:1.26-alpine AS runner
WORKDIR /src
COPY go/ .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/fd-runner ./cmd/fd-runner

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

# Codex, for the task labeller: GPT-6 Luna through `codex exec`. The static
# build at a pinned version; the operator signs in once by hand, as for claude,
# and the login lands in CODEX_HOME inside the odoo-data volume.
ARG CODEX_VERSION=0.155.1
RUN curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz" \
        | tar -xz -C /tmp \
    && install -m 0755 /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
    && rm -f /tmp/codex-x86_64-unknown-linux-musl \
    && codex --version

COPY --from=runner /out/fd-runner /usr/local/bin/fd-runner

# queue_job's runner needs these. `requests` ships with the image; the flag is
# Debian's, which refuses to install into a distribution-managed interpreter.
RUN pip install --no-cache-dir --break-system-packages openupgradelib

ENV CLAUDE_CONFIG_DIR=/var/lib/odoo/.claude
ENV CODEX_HOME=/var/lib/odoo/.codex

# The container runs as the host user's uid so it can read that user's session
# transcripts, which claude writes 0600. The data directory stays owned by the
# image's odoo user, so it is opened to the odoo group instead and the compose
# file keeps gid 101. A named volume created from here inherits this mode.
# CODEX_HOME has to exist before codex will start, even to sign in.
RUN mkdir -p /var/lib/odoo/.codex && chmod -R g+w /var/lib/odoo

USER odoo
