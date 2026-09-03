"""Claude Code local token-usage tracker.

Reads Claude Code's own local session transcripts (the officially supported
local mechanism) and records per-prompt token usage as JSONL, organised as
``data/usage/YYYY/MM/DD/<project>/prompts.jsonl``.

Nothing in this package talks to the network, and no credentials are ever read
or stored.
"""

__version__ = "1.0.0"

TRACKER_VERSION = __version__
