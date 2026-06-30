"""Async client for the Supergas Power self-service (Salesforce Aura) API.

This is an asyncio/aiohttp port of the standalone reference client. It talks
to the guest Salesforce Experience Cloud endpoint that backs the
invoice-payment page and returns the customer's invoice list.

Notes on the 2026 hardening (see repository README):
* ``getServiceAccount`` and ``getCustomerInvoices`` are rate-limited per
  source IP. Once the small per-window quota is spent they return
  ``state == "SUCCESS"`` with a ``null`` payload. That is surfaced here as
  :class:`SupergasThrottledError` so the coordinator can keep the last known
  value instead of crashing.
* The whole flow is sent as a single batched POST to be gentle on that quota.
* Never enumerate logistic numbers other than your own.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from aiohttp import ClientError, ClientSession

ORIGIN = "https://sfselfservice.supergas-power.co.il"
AURA_PATH = "/s/sfsites/aura?r=1&aura.ApexAction.execute=1"

# Apex methods whose null payload indicates the per-IP throttle (they carry
# PII); the routing helper legitimately returns short strings.
_PII_METHODS = ("getServiceAccount", "getCustomerInvoices")

STATUS_EN = {
    "שולם": "Paid",
    "לתשלום": "To pay",
    "שולם חלקית": "Partially paid",
}


class SupergasApiError(Exception):
    """Base error for the Supergas Power client."""


class SupergasThrottledError(SupergasApiError):
    """A PII method returned SUCCESS-but-null (per-IP rate limit)."""


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
    """Return the most recent invoice.

    Ordered by billing date (``EVENT_DATE``, ``YYYY-MM-DD`` so it sorts
    lexicographically) and then by the monotonically increasing invoice
    number as a tie-breaker.
    """
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
    """Minimal async client around the guest invoice endpoint."""

    def __init__(self, session: ClientSession, logistic: str, phone: str) -> None:
        self._session = session
        self._logistic = logistic
        self._phone = phone
        self._page_uri = f"/s/invoice-payment?logisticNumber={logistic}"

    async def _get_aura_context(self) -> dict:
        """Scrape the rotating ``fwuid``/``loaded`` build pins from the page."""
        try:
            async with self._session.get(
                ORIGIN + self._page_uri,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept-Language": "he-IL,he;q=0.9",
                },
                timeout=30,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
        except ClientError as err:
            raise SupergasApiError(f"Failed to load page: {err}") from err

        for match in re.finditer(r"/s/sfsites/l/([^/\"'>\s]+)/", html):
            try:
                blob = json.loads(urllib.parse.unquote(match.group(1)))
            except ValueError:
                continue
            if blob.get("fwuid") and blob.get("loaded"):
                return {
                    "fwuid": blob["fwuid"],
                    "app": blob.get("app", "siteforce:communityApp"),
                    "loaded": blob["loaded"],
                }
        raise SupergasApiError("Could not extract Aura context (fwuid/loaded)")

    async def _call_apex_batch(
        self, ctx: dict, calls: list[tuple[str, dict]]
    ) -> list[Any]:
        actions = [
            _apex_action(f"{i};a", method, params)
            for i, (method, params) in enumerate(calls, start=1)
        ]
        body = {
            "message": json.dumps({"actions": actions}, ensure_ascii=False),
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
        try:
            async with self._session.post(
                ORIGIN + AURA_PATH,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0",
                    "Origin": ORIGIN,
                    "Referer": ORIGIN + self._page_uri,
                },
                timeout=30,
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except ClientError as err:
            raise SupergasApiError(f"Apex request failed: {err}") from err

        try:
            payload = json.loads(text)
        except ValueError as err:
            raise SupergasApiError(
                "Non-JSON response (likely stale fwuid / aura:clientOutOfSync)"
            ) from err

        by_id = {a.get("id"): a for a in payload.get("actions", [])}
        results: list[Any] = []
        throttled: list[str] = []
        for i, (method, _params) in enumerate(calls, start=1):
            action = by_id.get(f"{i};a")
            if action is None:
                raise SupergasApiError(f"No response for action {method}")
            if action.get("state") != "SUCCESS":
                raise SupergasApiError(
                    f"Apex error in {method}: {action.get('error')}"
                )
            rv = action.get("returnValue")
            inner = rv.get("returnValue") if isinstance(rv, dict) else rv
            if inner is None and method in _PII_METHODS:
                throttled.append(method)
            results.append(inner)

        if throttled:
            raise SupergasThrottledError(
                "No data for " + ", ".join(throttled) + " (per-IP rate limit)"
            )
        return results

    async def async_fetch(self) -> dict[str, Any]:
        """Fetch account, eligibility and invoices in one round-trip.

        Returns a dict with ``account``, ``eligibility``, ``invoices`` and the
        derived ``latest_invoice``. Raises :class:`SupergasThrottledError` when
        the endpoint silently withholds data.
        """
        ctx = await self._get_aura_context()
        calls = [
            (
                "getServiceAccount",
                {
                    "logisticNumber": self._logistic,
                    "invoiceNumber": self._logistic,
                    "phone": self._phone,
                },
            ),
            (
                "canAccProcessInvoices",
                {"logisticNumber": self._logistic, "maaleSS": ""},
            ),
            (
                "getCustomerInvoices",
                {
                    "logisticNumber": self._logistic,
                    "optionalParams": json.dumps(
                        _invoice_filters(self._logistic), ensure_ascii=False
                    ),
                },
            ),
        ]
        account, eligibility, invoices = await self._call_apex_batch(ctx, calls)
        invoices = invoices or []
        return {
            "account": account,
            "eligibility": eligibility,
            "invoices": invoices,
            "latest_invoice": select_latest_invoice(invoices),
        }

    async def async_check_reachable(self) -> None:
        """Lightweight connectivity check for the config flow.

        Only confirms the site is reachable and the Aura context can be
        scraped (no PII quota is consumed). It cannot validate the phone,
        because a wrong phone is indistinguishable from the rate limit.
        """
        await self._get_aura_context()
