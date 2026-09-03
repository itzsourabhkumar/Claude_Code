"""Extract real token counts from Claude Code transcript records.

Claude Code stores the API's own ``usage`` object on every assistant record in
the session transcript, for example::

    "usage": {
      "input_tokens": 2,
      "cache_creation_input_tokens": 14014,
      "cache_read_input_tokens": 29857,
      "output_tokens": 374,
      "iterations": [ ... ]
    }

These are the actual counts reported by the API. Nothing here estimates,
approximates, or derives token counts from text length: a field that is absent
stays ``None`` so the dashboard can show it as unknown rather than as zero.

Note on ``iterations``: it is a per-request breakdown whose entries already roll
up into the top-level fields, so we read the top level only and never add the
two together.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: The four token dimensions Claude Code reports, in display order.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _coerce_int(value: Any) -> Optional[int]:
    """Return a non-negative int, or None when the value is not a real count."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def extract_usage(message: Any) -> Optional[Dict[str, Optional[int]]]:
    """Pull the four token counts out of an assistant ``message`` object.

    Returns None when the record carries no usage block at all (for example a
    streaming placeholder), so callers can tell "no data" from "all zeroes".
    """
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    extracted = {field: _coerce_int(usage.get(field)) for field in TOKEN_FIELDS}
    if all(value is None for value in extracted.values()):
        return None
    return extracted


def empty_usage() -> Dict[str, Optional[int]]:
    """A usage accumulator in which every dimension is still unknown."""
    return {field: None for field in TOKEN_FIELDS}


def add_usage(
    total: Dict[str, Optional[int]], part: Optional[Dict[str, Optional[int]]]
) -> Dict[str, Optional[int]]:
    """Accumulate one message's usage into a running total, in place.

    A dimension stays None until at least one message actually reports it, so a
    turn whose messages never reported (say) cache reads records None rather
    than a misleading 0.
    """
    if not part:
        return total
    for field in TOKEN_FIELDS:
        value = part.get(field)
        if value is None:
            continue
        total[field] = value if total.get(field) is None else total[field] + value
    return total


def total_tokens(usage: Dict[str, Optional[int]]) -> Optional[int]:
    """Sum of all four dimensions, or None when none of them is known.

    Cache reads and cache writes are billed differently from ordinary input
    tokens, but this figure is the total number of tokens that moved through the
    model for the turn, which is what the dashboard's "Total" column means.
    """
    known = [usage.get(field) for field in TOKEN_FIELDS]
    known = [value for value in known if value is not None]
    if not known:
        return None
    return sum(known)


def finalize_usage(usage: Dict[str, Optional[int]]) -> Dict[str, Optional[int]]:
    """Return the stored ``usage`` object, including ``total_tokens``."""
    result: Dict[str, Optional[int]] = {field: usage.get(field) for field in TOKEN_FIELDS}
    result["total_tokens"] = total_tokens(usage)
    return result


def cache_tokens(usage: Dict[str, Optional[int]]) -> Optional[int]:
    """Cache read + cache creation tokens combined."""
    values = [
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values)
