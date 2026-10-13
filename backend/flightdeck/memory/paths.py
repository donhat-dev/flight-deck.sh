"""Locating the auto-memory directory the way the harness locates it.

The harness keys memory on the project's canonical root, sanitised by replacing every
non-alphanumeric character with a dash: /home/x/Projects -> -home-x-Projects. Mirroring
that rule here (rather than taking a path from config) means these tools read exactly
the store the running agent reads, with nothing to keep in sync.
"""
import os
import re
from pathlib import Path

_MEMORY_DIRNAME = "memory"


def _config_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def sanitize(cwd: str) -> str:
    """The harness's project-key rule: every non-alphanumeric character becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def project_dir(cwd: str | None = None) -> Path:
    cwd = cwd or os.getcwd()
    return _config_home() / "projects" / sanitize(cwd)


def memory_dir(cwd: str | None = None) -> Path:
    return project_dir(cwd) / _MEMORY_DIRNAME


def transcript_dir(cwd: str | None = None) -> Path:
    """Session transcripts sit beside the memory directory, not inside it."""
    return project_dir(cwd)
