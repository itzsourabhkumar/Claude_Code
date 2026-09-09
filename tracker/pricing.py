"""Turn recorded token counts into money, per model, in USD and INR.

Design decision: **cost is derived, never stored.**

The JSONL tree records what Claude Code reported - token counts, which are facts
about a past interaction and never change. A cost is not a fact: it is those
counts multiplied by a price list and an exchange rate, both of which change
over time. Writing a rupee figure into an append-only file would freeze
yesterday's price into a record that can never be corrected, and would need a
migration every time Anthropic republishes its pricing page.

So costs are computed at read time, wherever usage is displayed, from the four
token counts already stored plus the model that produced them. Consequences:

* no JSONL schema change and no reindex - every interaction ever recorded gets a
  cost the moment this module lands;
* correcting a price or the exchange rate in ``config.json`` immediately fixes
  every historical figure;
* the JSONL tree remains the source of truth, exactly as before.

The four token dimensions are billed at four different rates, which is the whole
reason a single blended number would be wrong:

===========================  ====================================
Dimension                    Rate
===========================  ====================================
input_tokens                 base input price
cache_read_input_tokens      0.1x base input (0.025x on Fable 5.1)
cache_creation_input_tokens  1.25x base input (5m TTL), 2x (1h)
output_tokens                base output price
===========================  ====================================

Because cache reads are an order of magnitude cheaper than fresh input, and
cache writes a quarter more expensive, a tracker that priced "total tokens" at
the input rate would overstate a typical Claude Code bill by roughly ten times.

Prices verified against platform.claude.com on 2026-09-09; see PRICING_AS_OF.
Nothing here makes a network call - the table is static and user-overridable.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Dict, Iterable, List, Optional

from .token_parser import TOKEN_FIELDS

#: The day PRICES below was last checked against Anthropic's published pricing.
#: Shown in the dashboard and the CLI so a stale table is visible, not silent.
PRICING_AS_OF = "2026-09-09"

#: USD per million tokens, per model, per dimension.
#:
#: ``cache_write_5m`` / ``cache_write_1h`` are the two prompt-cache TTLs;
#: ``cache_read`` is a cache hit. Anthropic's general rule is 1.25x base input
#: for a 5-minute write, 2x for a one-hour write and 0.1x for a read - but
#: Fable 5.1 and Mythos 5.1 read at 0.025x, so every price is written out in
#: full rather than derived from a multiplier that has exceptions.
PRICES: Dict[str, Dict[str, float]] = {
    # Fable tier - note the 0.025x cache read, not 0.1x.
    "claude-fable-5-1":  {"input": 10.0, "output": 50.0, "cache_write_5m": 12.50, "cache_write_1h": 20.0, "cache_read": 0.25},
    "claude-mythos-5-1": {"input": 10.0, "output": 50.0, "cache_write_5m": 12.50, "cache_write_1h": 20.0, "cache_read": 0.25},
    "claude-fable-5":    {"input": 10.0, "output": 50.0, "cache_write_5m": 12.50, "cache_write_1h": 20.0, "cache_read": 1.00},
    "claude-mythos-5":   {"input": 10.0, "output": 50.0, "cache_write_5m": 12.50, "cache_write_1h": 20.0, "cache_read": 1.00},
    # Opus tier.
    "claude-opus-5":     {"input":  5.0, "output": 25.0, "cache_write_5m":  6.25, "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-8":   {"input":  5.0, "output": 25.0, "cache_write_5m":  6.25, "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-7":   {"input":  5.0, "output": 25.0, "cache_write_5m":  6.25, "cache_write_1h": 10.0, "cache_read": 0.50},
    "claude-opus-4-6":   {"input":  5.0, "output": 25.0, "cache_write_5m":  6.25, "cache_write_1h": 10.0, "cache_read": 0.50},
    # Sonnet tier.
    "claude-sonnet-5":   {"input":  2.0, "output": 10.0, "cache_write_5m":  2.50, "cache_write_1h":  4.0, "cache_read": 0.20},
    "claude-sonnet-4-6": {"input":  3.0, "output": 15.0, "cache_write_5m":  3.75, "cache_write_1h":  6.0, "cache_read": 0.30},
    # Haiku tier.
    "claude-haiku-4-5":  {"input":  1.0, "output":  5.0, "cache_write_5m":  1.25, "cache_write_1h":  2.0, "cache_read": 0.10},
}

#: Static USD -> INR rate. Deliberately not fetched: this tracker makes no
#: network calls, and a rate that changed under the user would make yesterday's
#: dashboard disagree with today's for the same data. Override it in
#: ``config.json`` (``pricing.usd_to_inr``) with whatever rate you want to
#: account at - the dashboard always shows which rate produced the figures.
DEFAULT_USD_TO_INR = 88.0

#: Which prompt-cache TTL to assume for cache-creation tokens. Claude Code's
#: transcripts record *how many* tokens were written to the cache but not the
#: TTL they were written with, so this cannot be detected - it is a documented
#: assumption. 5m is Claude Code's default and the cheaper of the two, so an
#: unusual 1h workload is understated rather than the common case overstated.
DEFAULT_CACHE_WRITE_TTL = "5m"

#: Model ids that are not real models and must never be priced. Claude Code
#: writes "<synthetic>" for locally generated assistant turns that never hit the
#: API and therefore cost nothing to run.
SYNTHETIC_MODELS = frozenset({"<synthetic>", "synthetic"})

#: Trailing date snapshot on an otherwise known id, e.g.
#: "claude-haiku-4-5-20251001" -> "claude-haiku-4-5".
_DATE_SUFFIX = re.compile(r"-\d{8}$")

#: The three buckets the UI reports, and which stored token fields feed each.
#: "cached_input" carries both cache dimensions: a cache read and a cache write
#: are both input tokens that went through the cache, they simply price
#: differently, and the per-dimension detail is kept alongside the bucket.
COST_BUCKETS = ("input", "cached_input", "output")


def normalise_model(model: Optional[str]) -> Optional[str]:
    """Reduce a model id to the key used in :data:`PRICES`.

    Handles the dated-snapshot form Claude Code sometimes records
    (``claude-haiku-4-5-20251001``) and the ``anthropic.``/``@`` decorations the
    Bedrock and Vertex ids carry, so the same price applies wherever a session
    ran.
    """
    if not isinstance(model, str):
        return None
    text = model.strip()
    if not text:
        return None
    text = text.split("@", 1)[0]              # Vertex: claude-x@20260101
    for prefix in ("anthropic.", "anthropic/"):
        if text.startswith(prefix):           # Bedrock: anthropic.claude-x
            text = text[len(prefix):]
    return text or None


def _lookup(model: Optional[str], table: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Find a model's prices, trying the exact id then the undated id."""
    key = normalise_model(model)
    if key is None or key in SYNTHETIC_MODELS:
        return None
    if key in table:
        return table[key]
    undated = _DATE_SUFFIX.sub("", key)
    return table.get(undated)


def _coerce_price_row(raw: Any) -> Optional[Dict[str, float]]:
    """Validate one user-supplied price row from ``config.json``.

    A malformed override is ignored rather than raising: bad pricing must leave
    the tracker reporting "unpriced", never stop it recording usage.
    """
    if not isinstance(raw, dict):
        return None
    row: Dict[str, float] = {}
    for field in ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read"):
        value = raw.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < 0:
            continue
        row[field] = float(value)
    if "input" not in row or "output" not in row:
        return None
    # Fall back to Anthropic's standard multipliers for any cache price the user
    # left out, so an override only has to state input and output.
    row.setdefault("cache_write_5m", row["input"] * 1.25)
    row.setdefault("cache_write_1h", row["input"] * 2.0)
    row.setdefault("cache_read", row["input"] * 0.1)
    return row


def _clean(value: Any) -> Optional[int]:
    """A token count we can price, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    return None


class PriceBook:
    """Prices tokens, in USD and INR, for one configuration.

    Construct once per request or command and reuse it: resolution is a dict
    lookup, but the object also carries the exchange rate and TTL choice so no
    caller has to thread them through.
    """

    def __init__(
        self,
        usd_to_inr: float = DEFAULT_USD_TO_INR,
        cache_write_ttl: str = DEFAULT_CACHE_WRITE_TTL,
        overrides: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ):
        self.usd_to_inr = float(usd_to_inr) if usd_to_inr and usd_to_inr > 0 else DEFAULT_USD_TO_INR
        self.cache_write_ttl = "1h" if str(cache_write_ttl).lower() in ("1h", "60m", "hour") else "5m"
        self.enabled = bool(enabled)

        self.prices: Dict[str, Dict[str, float]] = dict(PRICES)
        for model, row in (overrides or {}).items():
            cleaned = _coerce_price_row(row)
            if cleaned is not None:
                self.prices[str(model)] = cleaned

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config) -> "PriceBook":
        """Build from a :class:`tracker.config.Config`."""
        return cls(
            usd_to_inr=config.usd_to_inr,
            cache_write_ttl=config.cache_write_ttl,
            overrides=config.model_prices,
            enabled=config.pricing_enabled,
        )

    # ------------------------------------------------------------------
    @property
    def cache_write_field(self) -> str:
        return "cache_write_5m" if self.cache_write_ttl == "5m" else "cache_write_1h"

    def prices_for(self, model: Optional[str]) -> Optional[Dict[str, float]]:
        """USD-per-million prices for a model, or None when it is unpriced."""
        return _lookup(model, self.prices)

    def is_priced(self, model: Optional[str]) -> bool:
        return self.prices_for(model) is not None

    def to_inr(self, usd: Optional[float]) -> Optional[float]:
        return None if usd is None else usd * self.usd_to_inr

    def known_models(self) -> List[str]:
        return sorted(self.prices)

    # ------------------------------------------------------------------
    def cost_for(self, usage: Any, model: Optional[str]) -> Dict[str, Any]:
        """Cost one set of token counts produced by one model.

        ``usage`` may be the stored ``usage`` object or any mapping carrying the
        four token fields, so this works on a JSONL record, a database row and a
        grouped aggregate alike.

        Returns the three buckets in USD and INR plus the total, and always sets
        ``priced``: False means the model has no price and every figure is None
        rather than a guess. Token counts are echoed back so a reader can always
        see what produced the number.
        """
        usage = usage if isinstance(usage, dict) else {}
        counts = {field: _clean(usage.get(field)) for field in TOKEN_FIELDS}
        prices = self.prices_for(model)

        result: Dict[str, Any] = {
            "model": normalise_model(model),
            "priced": prices is not None,
            "currency": "INR",
            "usd_to_inr": self.usd_to_inr,
            "cache_write_ttl": self.cache_write_ttl,
            "tokens": dict(counts),
        }

        if prices is None:
            for bucket in COST_BUCKETS:
                result[bucket + "_cost_usd"] = None
                result[bucket + "_cost_inr"] = None
            result["cache_read_cost_usd"] = None
            result["cache_write_cost_usd"] = None
            result["cache_read_cost_inr"] = None
            result["cache_write_cost_inr"] = None
            result["total_cost_usd"] = None
            result["total_cost_inr"] = None
            result["unpriced_tokens"] = sum(v for v in counts.values() if v)
            return result

        per_token = 1_000_000.0
        input_usd = (counts["input_tokens"] or 0) * prices["input"] / per_token
        output_usd = (counts["output_tokens"] or 0) * prices["output"] / per_token
        read_usd = (counts["cache_read_input_tokens"] or 0) * prices["cache_read"] / per_token
        write_usd = (
            (counts["cache_creation_input_tokens"] or 0)
            * prices[self.cache_write_field] / per_token
        )
        cached_usd = read_usd + write_usd

        # The total is the sum of the parts it is displayed beside - never an
        # independently derived figure that could disagree with them.
        total_usd = input_usd + cached_usd + output_usd

        result.update({
            "input_cost_usd": input_usd,
            "cached_input_cost_usd": cached_usd,
            "output_cost_usd": output_usd,
            "cache_read_cost_usd": read_usd,
            "cache_write_cost_usd": write_usd,
            "total_cost_usd": total_usd,
            "input_cost_inr": self.to_inr(input_usd),
            "cached_input_cost_inr": self.to_inr(cached_usd),
            "output_cost_inr": self.to_inr(output_usd),
            "cache_read_cost_inr": self.to_inr(read_usd),
            "cache_write_cost_inr": self.to_inr(write_usd),
            "total_cost_inr": self.to_inr(total_usd),
            "unpriced_tokens": 0,
            "rates": dict(prices),
        })
        return result

    # ------------------------------------------------------------------
    def aggregate(self, rows: Iterable[Any], model_key: str = "model") -> Dict[str, Any]:
        """Sum costs across rows that each carry one model's token totals.

        This is why the database groups by model before anything is priced: a
        filter spanning Opus and Haiku has no single rate, so each model's
        tokens are costed at its own prices and only the money is added up.

        Rows whose model has no price contribute their tokens to
        ``unpriced_tokens`` and nothing to the cost, so a partially priced
        selection reports an honest subtotal plus how much it could not cover.
        """
        totals = {
            "input_cost_usd": 0.0,
            "cached_input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "cache_read_cost_usd": 0.0,
            "cache_write_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }
        unpriced_tokens = 0
        unpriced_models: set = set()
        priced_any = False

        for row in rows:
            row = row if isinstance(row, dict) else dict(row)
            model = row.get(model_key)
            # A grouped row carries the token fields at the top level; a stored
            # record nests them under "usage". Accept both.
            usage = row.get("usage") if isinstance(row.get("usage"), dict) else row
            costed = self.cost_for(usage, model)

            if not costed["priced"]:
                unpriced_tokens += costed.get("unpriced_tokens") or 0
                if costed.get("tokens") and any(costed["tokens"].values()):
                    unpriced_models.add(normalise_model(model) or "unknown")
                continue

            priced_any = True
            for key in totals:
                totals[key] += costed[key] or 0.0

        # Nothing in this group had a price: report unknown, not free. A zero
        # here would read as "this model cost nothing", which is a different
        # claim from "we do not know what this model costs".
        if not priced_any:
            result = {key: None for key in totals}
            result.update({key.replace("_usd", "_inr"): None for key in totals})
            result.update({
                "currency": "INR",
                "usd_to_inr": self.usd_to_inr,
                "cache_write_ttl": self.cache_write_ttl,
                "pricing_as_of": PRICING_AS_OF,
                "priced": False,
                "unpriced_tokens": unpriced_tokens,
                "unpriced_models": sorted(unpriced_models),
            })
            return result

        result: Dict[str, Any] = dict(totals)
        for key in list(totals):
            result[key.replace("_usd", "_inr")] = self.to_inr(totals[key])
        result.update({
            "currency": "INR",
            "usd_to_inr": self.usd_to_inr,
            "cache_write_ttl": self.cache_write_ttl,
            "pricing_as_of": PRICING_AS_OF,
            "priced": priced_any,
            "unpriced_tokens": unpriced_tokens,
            "unpriced_models": sorted(unpriced_models),
        })
        return result


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def rupee_symbol(stream: Any = None) -> str:
    """``₹`` where it can be displayed, ``Rs.`` where it cannot.

    A Windows console commonly runs a cp1252 code page, which has no rupee sign;
    printing one there raises ``UnicodeEncodeError`` and would crash the CLI.
    The symbol is therefore chosen from what the destination stream can actually
    encode. Only the terminal needs this - the dashboard is UTF-8 throughout.
    """
    target = stream if stream is not None else getattr(sys, "stdout", None)
    encoding = getattr(target, "encoding", None) or "ascii"
    try:
        "₹".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return "Rs."
    return "₹"


def format_inr(amount: Optional[float], decimals: int = 2, symbol: Optional[str] = None) -> str:
    """Render rupees the way India writes them: ``₹12,34,567.89``.

    The Indian digit grouping is 2,2,3 from the right, not 3,3,3, so
    ``f"{n:,}"`` produces a number that reads wrong to the audience this feature
    is for. Pass ``symbol`` to force a prefix (see :func:`rupee_symbol`).
    """
    if amount is None:
        return "-"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "-"
    prefix = "₹" if symbol is None else symbol

    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    fraction = value - whole

    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        grouped = ",".join(groups) + "," + tail
    else:
        grouped = digits

    if decimals > 0:
        return "%s%s%s%s" % (sign, prefix, grouped, ("%.*f" % (decimals, fraction))[1:])
    return "%s%s%s" % (sign, prefix, grouped)
