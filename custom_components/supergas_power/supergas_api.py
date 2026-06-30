"""Client for the Supergas Power self-service (Salesforce Aura) API.

The self-service page is a Salesforce Experience Cloud (Aura) site. To read a
guest's invoices the page performs an ordered sequence of Apex actions:

1. ``processIdentity`` — authorises the current (cookie-backed) session for a
   given ``logisticNumber`` + ``phone``. **This must run first.** Until it
   does, the identity-gated methods below return ``state == "SUCCESS"`` with a
   ``null`` payload, which looks like throttling but is really "identity not
   established yet".
2. ``getServiceAccount`` — returns the account, including
   ``Maale_Secondary_Status__c`` (the "maaleSS" value).
3. ``canAccProcessInvoices`` — eligibility check; needs ``maaleSS`` from (2).
4. ``getCustomerInvoices`` — the invoice list the sensor needs.

The browser issues these as *separate, sequential* requests (``r=0,1,2,…``)
rather than one batch, so that the identity from step 1 is committed before
step 2 runs. We mirror that here.

There is no TLS/HTTP2 fingerprint bot-protection on this endpoint (a plain
client receives the same data as a real browser once ``processIdentity`` has
run), so this client uses :mod:`aiohttp` — the HTTP stack already bundled with
Home Assistant — and is fully asynchronous. Pass it the shared
``aiohttp.ClientSession`` from ``async_get_clientsession(hass)``.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

ORIGIN = "https://sfselfservice.supergas-power.co.il"

# Be explicit about the User-Agent rather than relying on the shared session's
# default; the site is happier with a normal browser UA.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Per-request timeout (seconds) for both the page GET and each Apex POST.
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _aura_path(r: int) -> str:
    """Aura Apex endpoint with the per-request counter the browser sends."""
    return f"/s/sfsites/aura?r={r}&aura.ApexAction.execute=1"


STATUS_EN = {
    "שולם": "Paid",
    "לתשלום": "To pay",
    "שולם חלקית": "Partially paid",
}


def _mask(value: str) -> str:
    """Mask a logistic/phone for logs: keep first 3 and last 2 digits."""
    if not value:
        return "<empty>"
    if len(value) <= 5:
        return value[0] + "***"
    return f"{value[:3]}***{value[-2:]}"


class SupergasApiError(Exception):
    """Base error for the Supergas Power client."""


class SupergasThrottledError(SupergasApiError):
    """A required method returned SUCCESS-but-null (identity not established)."""


def _apex_action(action_id: str, method: str, params: dict) -> dict:
    return {
        "id": action_id,
        "descriptor": "aura://ApexActionController/ACTION$execute",
        "callingDescriptor": "UNKNOWN",
        "params": {
            "namespace": "",
            "classname": "SelfServiceController",
            "method": method,
            "params": params,
            "cacheable": False,
            "isContinuation": False,
        },
    }


def _invoice_filters(logistic: str) -> list[dict]:
    return [
        {"paramKey": "FIRM_CODE", "paramValue": "1",
         "paramOperator": "=", "paramType": "string"},
        {"paramKey": "Customer_code", "paramValue": logistic,
         "paramOperator": "=", "paramType": "string"},
    ]


def select_latest_invoice(invoices: list[dict]) -> dict | None:
    """Return the most recent invoice (latest billing date, then number)."""
    if not invoices:
        return None

    def key(inv: dict) -> tuple[str, float]:
        event_date = inv.get("EVENT_DATE") or ""
        try:
            number = float(inv.get("INVOICE_NUMBER") or 0)
        except (TypeError, ValueError):
            number = 0.0
        return (event_date, number)

    return max(invoices, key=key)


class SupergasClient:
    """Async client around the guest invoice endpoint.

    Construct with an :class:`aiohttp.ClientSession` (e.g. the shared one from
    ``homeassistant.helpers.aiohttp_client.async_get_clientsession``) and
    ``await`` :meth:`fetch` / :meth:`check_reachable` directly.
    """

    def __init__(
        self, session: aiohttp.ClientSession, logistic: str, phone: str
    ) -> None:
        self._session = session
        self._logistic = logistic
        self._phone = phone
        self._page_uri = f"/s/invoice-payment?logisticNumber={logistic}"
        self._r = 0

    async def _get_aura_context(self) -> dict:
        """Scrape the rotating ``fwuid``/``loaded`` build pins from the page."""
        url = ORIGIN + self._page_uri
        _LOGGER.debug("GET aura context: %s (logistic=%s)", url, _mask(self._logistic))
        try:
            async with self._session.get(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept-Language": "he-IL,he;q=0.9",
                },
                timeout=_TIMEOUT,
            ) as resp:
                status = resp.status
                html = await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("GET page failed: %r", err)
            raise SupergasApiError(f"Failed to load page: {err}") from err

        _LOGGER.debug("GET page -> HTTP %s, %d bytes", status, len(html))
        if status >= 400:
            _LOGGER.warning("GET page returned HTTP %s; snippet: %s", status, html[:300])
            raise SupergasApiError(f"Page load returned HTTP {status}")

        for match in re.finditer(r"/s/sfsites/l/([^/\"'>\s]+)/", html):
            try:
                blob = json.loads(urllib.parse.unquote(match.group(1)))
            except ValueError:
                continue
            if blob.get("fwuid") and blob.get("loaded"):
                ctx = {
                    "fwuid": blob["fwuid"],
                    "app": blob.get("app", "siteforce:communityApp"),
                    "loaded": blob["loaded"],
                }
                _LOGGER.debug(
                    "Aura context found: fwuid=%s app=%s loaded=%s",
                    ctx["fwuid"], ctx["app"], ctx["loaded"],
                )
                return ctx
        _LOGGER.warning(
            "Aura context (fwuid/loaded) not found in page. HTML snippet: %s",
            html[:400],
        )
        raise SupergasApiError("Could not extract Aura context (fwuid/loaded)")

    async def _call_apex(
        self,
        ctx: dict,
        method: str,
        params: dict,
        *,
        required: bool = False,
    ) -> Any:
        """Execute a single Apex action as its own request (browser-style).

        Returns the unwrapped ``returnValue``. When ``required`` is set and the
        server replies SUCCESS-but-null, raise :class:`SupergasThrottledError`
        (this normally means ``processIdentity`` did not run / did not match).
        """
        self._r += 1
        action = _apex_action("1;a", method, params)
        body = urllib.parse.urlencode(
            {
                "message": json.dumps({"actions": [action]}, ensure_ascii=False),
                "aura.context": json.dumps(
                    {
                        "mode": "PROD",
                        "fwuid": ctx["fwuid"],
                        "app": ctx["app"],
                        "loaded": ctx["loaded"],
                        "dn": [],
                        "globals": {},
                        "uad": True,
                    },
                    ensure_ascii=False,
                ),
                "aura.pageURI": self._page_uri,
                "aura.token": "null",
            }
        ).encode("utf-8")

        _LOGGER.debug("POST apex method=%s (r=%d)", method, self._r)
        try:
            async with self._session.post(
                ORIGIN + _aura_path(self._r),
                data=body,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": ORIGIN,
                    "Referer": ORIGIN + self._page_uri,
                    "X-SFDC-LDS-Endpoints": (
                        f"ApexActionController.execute:SelfServiceController.{method}"
                    ),
                },
                timeout=_TIMEOUT,
            ) as resp:
                status = resp.status
                text = await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("POST apex failed: %r", err)
            raise SupergasApiError(f"Apex request failed: {err}") from err

        _LOGGER.debug("POST apex %s -> HTTP %s, %d bytes", method, status, len(text))
        if status >= 400:
            _LOGGER.warning("POST apex returned HTTP %s; snippet: %s", status, text[:400])
            raise SupergasApiError(f"Apex request returned HTTP {status}")

        try:
            payload = json.loads(text)
        except ValueError as err:
            _LOGGER.warning(
                "Apex response was not JSON (stale fwuid / clientOutOfSync?). "
                "Snippet: %s",
                text[:400],
            )
            raise SupergasApiError(
                "Non-JSON response (likely stale fwuid / aura:clientOutOfSync)"
            ) from err

        actions = payload.get("actions", [])
        result = actions[0] if actions else None
        if result is None:
            _LOGGER.warning("No response entry for action %s", method)
            raise SupergasApiError(f"No response for action {method}")
        state = result.get("state")
        if state != "SUCCESS":
            _LOGGER.warning(
                "Apex method %s state=%s error=%s", method, state, result.get("error")
            )
            raise SupergasApiError(f"Apex error in {method}: {result.get('error')}")

        rv = result.get("returnValue")
        inner = rv.get("returnValue") if isinstance(rv, dict) else rv
        kind = (
            f"list[{len(inner)}]" if isinstance(inner, list)
            else "null" if inner is None
            else type(inner).__name__
        )
        _LOGGER.debug("Apex method %s -> state=%s, returnValue=%s", method, state, kind)

        if inner is None and required:
            _LOGGER.warning(
                "Required method %s returned null (identity not established, or "
                "logistic/phone not matched). Raw response (truncated): %s",
                method,
                text[:800],
            )
            raise SupergasThrottledError(
                f"No data for {method} (identity not established / not matched)"
            )
        return inner

    async def fetch(self) -> dict[str, Any]:
        """Fetch account, eligibility and invoices for the configured guest."""
        _LOGGER.debug(
            "Fetch start: logistic=%s phone=%s",
            _mask(self._logistic), _mask(self._phone),
        )
        self._r = 0
        ctx = await self._get_aura_context()

        # 1. Establish identity for this session. Everything below is gated on
        #    this; without it the account/invoice methods return null.
        identity_ok = await self._call_apex(
            ctx,
            "processIdentity",
            {
                "identityType": "phone",
                "logisticNumber": self._logistic,
                "identityValue": self._phone,
            },
            required=True,
        )
        if not identity_ok:
            raise SupergasThrottledError(
                "processIdentity returned false (logistic/phone not matched)"
            )

        # 2. Account (carries the Maale secondary status used in step 3).
        account = await self._call_apex(
            ctx,
            "getServiceAccount",
            {
                "logisticNumber": self._logistic,
                "invoiceNumber": self._logistic,
                "phone": self._phone,
            },
        )
        maale_ss = ""
        if isinstance(account, dict):
            maale_ss = account.get("Maale_Secondary_Status__c") or ""

        # 3. Eligibility check (auxiliary; null here is tolerated).
        eligibility = await self._call_apex(
            ctx,
            "canAccProcessInvoices",
            {"logisticNumber": self._logistic, "maaleSS": maale_ss},
        )

        # 4. The invoice list the sensor actually needs.
        invoices = await self._call_apex(
            ctx,
            "getCustomerInvoices",
            {
                "logisticNumber": self._logistic,
                "optionalParams": json.dumps(
                    _invoice_filters(self._logistic), ensure_ascii=False
                ),
            },
            required=True,
        )

        invoices = invoices or []
        latest = select_latest_invoice(invoices)
        _LOGGER.debug(
            "Fetch done: account=%s eligibility=%s invoices=%d latest_amount=%s",
            "present" if account else "none",
            eligibility,
            len(invoices),
            (latest or {}).get("INVOICE_AMOUNT") if latest else None,
        )
        return {
            "account": account,
            "eligibility": eligibility,
            "invoices": invoices,
            "latest_invoice": latest,
        }

    async def check_reachable(self) -> None:
        """Lightweight connectivity check for the config flow."""
        _LOGGER.debug("Reachability check (logistic=%s)", _mask(self._logistic))
        await self._get_aura_context()
