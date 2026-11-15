"""v8.1 slice 9.1: deal offers (cinema discount days, bundles, memberships, streaming offers), kit contract deal_offer v1.

Switch DEALS (off by default): while it is off shown() returns nothing. Nothing imports this module yet and it holds NO
real offers: there is no fetcher and no table of offers. Real offers need a named official source checked by a person.
Rules built in (the kit's): an offer existing is not a confirmed ticket price; a price is shown only when the record says
where it came from AND it is verified AND not expired AND checked recently; otherwise the price is 'unknown'.
Nothing here ever computes a price, a percentage off or a saving."""
import os
import re
from datetime import datetime, timedelta, timezone

ENABLED = os.environ.get("DEALS", "0") == "1"
SCHEMA_VERSION = 1
RECHECK_DAYS = int(os.environ.get("DEALS_RECHECK_DAYS", "30"))
OFFER_TYPES = ("discount_day", "bundle", "loyalty", "subscription", "student", "matinee", "streaming_rental",
               "streaming_subscription", "other")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PRICE_STATUS = ("unknown", "published_offer", "verified_checkout")
VERIFICATION = ("unverified", "verified", "expired", "disputed")
FIELDS = ("schema_version", "offer_id", "provider_name", "offer_type", "title", "region", "location_ids",
          "eligible_weekdays", "eligibility", "exclusions", "promotion_terms", "price_status", "price", "currency",
          "source_url", "last_verified_at", "verification_status", "valid_from", "valid_until")
_CUR = re.compile(r"^[A-Z]{3}$")
_URL = re.compile(r"^https://[^\s/]+\.[^\s/]+(/\S*)?$")
PLACEHOLDER_HOSTS = ("example.com", "example.org", "example.net", "localhost")


class OfferError(ValueError):
    pass


def _dt(s, name):
    if not isinstance(s, str):
        raise OfferError("%s must be a date-time" % name)
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        raise OfferError("%s is not a date-time" % name)
    if d.tzinfo is None:
        raise OfferError("%s needs a time zone" % name)
    return d


def _texts(v, name, unique=False, allowed=None, nonempty=False):
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        raise OfferError("%s must be a list of text" % name)
    if nonempty and any(not x for x in v):
        raise OfferError("%s has an empty entry" % name)
    if unique and len(set(v)) != len(v):
        raise OfferError("%s has a repeated entry" % name)
    if allowed and any(x not in allowed for x in v):
        raise OfferError("%s has an unknown value" % name)


def validate(o):
    """Raises OfferError unless o matches the kit's deal_offer v1 exactly, plus two consistency rules the schema
    cannot express: a price needs a currency and a non-unknown price_status; 'unknown' means no price at all."""
    if not isinstance(o, dict):
        raise OfferError("offer must be an object")
    extra = set(o) - set(FIELDS)
    if extra:
        raise OfferError("unknown fields: %s" % ", ".join(sorted(extra)))
    missing = [k for k in FIELDS if k not in o]
    if missing:
        raise OfferError("missing fields: %s" % ", ".join(missing))
    if o["schema_version"] != 1 or isinstance(o["schema_version"], bool):
        raise OfferError("schema_version must be 1")
    for k in ("offer_id", "provider_name", "title", "promotion_terms"):
        if not isinstance(o[k], str) or not o[k]:
            raise OfferError("%s must be non-empty text" % k)
    if not isinstance(o["region"], str) or len(o["region"]) < 2:
        raise OfferError("region too short")
    if o["offer_type"] not in OFFER_TYPES:
        raise OfferError("unknown offer_type")
    _texts(o["location_ids"], "location_ids", unique=True, nonempty=True)
    _texts(o["eligible_weekdays"], "eligible_weekdays", unique=True, allowed=WEEKDAYS)
    _texts(o["eligibility"], "eligibility")
    _texts(o["exclusions"], "exclusions")
    if o["price_status"] not in PRICE_STATUS:
        raise OfferError("unknown price_status")
    if o["verification_status"] not in VERIFICATION:
        raise OfferError("unknown verification_status")
    p = o["price"]
    if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float)) or p < 0 or p != p):
        raise OfferError("price must be a number >= 0 or null")
    cur = o["currency"]
    if cur is not None and (not isinstance(cur, str) or not _CUR.match(cur)):
        raise OfferError("currency must be a three-letter code or null")
    if not isinstance(o["source_url"], str) or not _URL.match(o["source_url"]):
        raise OfferError("source_url must be an https address")
    _dt(o["last_verified_at"], "last_verified_at")
    a = _dt(o["valid_from"], "valid_from") if o["valid_from"] is not None else None
    b = _dt(o["valid_until"], "valid_until") if o["valid_until"] is not None else None
    if a and b and b < a:
        raise OfferError("valid_until is before valid_from")
    if p is not None and cur is None:
        raise OfferError("a price needs a currency")
    if p is not None and o["price_status"] == "unknown":
        raise OfferError("price_status unknown cannot carry a price")
    return o


def is_placeholder(o):
    host = o["source_url"].split("/")[2].lower()
    return any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS)


def state(o, now=None):
    """'active' | 'not_started' | 'expired' | 'needs_recheck' | 'unverified' | 'disputed' at the given moment."""
    now = now or datetime.now(timezone.utc)
    if o["verification_status"] == "disputed":
        return "disputed"
    if o["verification_status"] == "expired" or (o["valid_until"] and _dt(o["valid_until"], "valid_until") < now):
        return "expired"
    if o["verification_status"] != "verified":
        return "unverified"
    if now - _dt(o["last_verified_at"], "last_verified_at") > timedelta(days=RECHECK_DAYS):
        return "needs_recheck"
    if o["valid_from"] and _dt(o["valid_from"], "valid_from") > now:
        return "not_started"
    return "active"


def card(o, now=None):
    """What a page may show for one offer. price is None ('unknown') unless every condition holds; the page links to
    the official source and never says a ticket can be bought here."""
    validate(o)
    st = state(o, now)
    show_price = st == "active" and o["price"] is not None and o["price_status"] in ("published_offer", "verified_checkout")
    return {"offer_id": o["offer_id"], "provider": o["provider_name"], "title": o["title"], "type": o["offer_type"],
            "region": o["region"], "weekdays": list(o["eligible_weekdays"]), "eligibility": list(o["eligibility"]),
            "exclusions": list(o["exclusions"]), "terms": o["promotion_terms"], "state": st,
            "price": o["price"] if show_price else None, "currency": o["currency"] if show_price else None,
            "price_label": ({"published_offer": "advertised price", "verified_checkout": "price seen at checkout"}[o["price_status"]]
                            if show_price else "price unknown - check the official page"),
            "caveat": "Taxes, fees and premium formats may cost extra. Showtimes and seats are not checked.",
            "source_url": o["source_url"], "last_verified_at": o["last_verified_at"]}


def shown(offers, region=None, weekday=None, now=None, enabled=None):
    """Cards a page may list: nothing while DEALS is off; only valid, active, non-placeholder offers; region is an exact
    match or a parent ('US' matches 'US-MA'); weekday keeps offers for that day or with no day limit."""
    if not (ENABLED if enabled is None else enabled):
        return []
    out = []
    for o in offers:
        try:
            validate(o)
        except OfferError:
            continue
        if is_placeholder(o) or state(o, now) != "active":
            continue
        if region and not (o["region"] == region or region.startswith(o["region"] + "-") or o["region"].startswith(region + "-")):
            continue
        if weekday and o["eligible_weekdays"] and weekday not in o["eligible_weekdays"]:
            continue
        out.append(card(o, now))
    return out
