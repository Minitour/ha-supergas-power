"""Client for the Supergas Power self-service (Salesforce Aura) API.

The endpoint sits behind bot protection that fingerprints the TLS/HTTP2
handshake: a real browser gets data, while a plain ``requests``/``aiohttp``
client receives ``state == "SUCCESS"`` with a ``null`` payload (it is *not*
IP rate-limiting and *not* a request-shape issue — verified by getting data
from a real browser and from ``curl_cffi`` at the same instant a vanilla
client was being nulled).

We therefore use :mod:`curl_cffi`, which impersonates Chrome's real TLS/HTTP2
fingerprint, to defeat that detection. The client is synchronous and is meant
to be run inside Home Assistant's executor (see the coordinator).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from curl_cffi import requests as cffi_requests

# curl_cffi moved its exception locations between releases; stay version-robust.
try:  # pragma: no cover - import shim
    from curl_cffi.requests.exceptions import RequestException as _HttpError
except Exception:  # noqa: BLE001
    try:  # pragma: no cover
        from curl_cffi.requests.errors import RequestsError as _HttpError
    except Exception:  # noqa: BLE001
        _HttpError = Exception

ORIGIN = "https://sfselfservice.supergas-power.co.il"
AURA_PATH = "/s/sfsites/aura?r=1&aura.ApexAction.execute=1"

# Browser profile for curl_cffi to impersonate (TLS + HTTP2 fingerprint).
IMPERSONATE = "chrome"

# Only the invoice list is required for the sensor. ``getServiceAccount`` is
# auxiliary, so a null there must not fail the whole fetch.
_REQUIRED_METHODS = ("getCustomerInvoices",)

STATUS_EN = {
    "שולם": "Paid",
    "לתשלום": "To pay",
    "שולם חלקית": "Partially paid",
}


class SupergasApiError(Exception):
    """Base error for the Supergas Power client."""


class SupergasThrottledError(SupergasApiError):
    """A required method returned SUCCESS-but-null (bot-blocked / throttled)."""


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
    """Synchronous client around the guest invoice endpoint.

    Run its blocking methods (:meth:`fetch`, :meth:`check_reachable`) inside an
    executor from Home Assistant.
    """

    def __init__(self, logistic: str, phone: str) -> None:
        self._logistic = logistic
        self._phone = phone
        self._page_uri = f"/s/invoice-payment?logisticNumber={logistic}"

    def _new_session(self) -> "cffi_requests.Session":
        return cffi_requests.Session(impersonate=IMPERSONATE, timeout=30)

    def _get_aura_context(self, session: "cffi_requests.Session") -> dict:
        """Scrape the rotating ``fwuid``/``loaded`` build pins from the page."""
        try:
            resp = session.get(
                ORIGIN + self._page_uri,
                headers={"Accept-Language": "he-IL,he;q=0.9"},
            )
            resp.raise_for_status()
            html = resp.text
        except _HttpError as err:
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

    def _call_apex_batch(
        self,
        session: "cffi_requests.Session",
        ctx: dict,
        calls: list[tuple[str, dict]],
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
            resp = session.post(
                ORIGIN + AURA_PATH,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": ORIGIN,
                    "Referer": ORIGIN + self._page_uri,
                },
            )
            resp.raise_for_status()
            text = resp.text
        except _HttpError as err:
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
            if inner is None and method in _REQUIRED_METHODS:
                throttled.append(method)
            results.append(inner)

        if throttled:
            raise SupergasThrottledError(
                "No data for " + ", ".join(throttled) + " (bot-blocked / throttled)"
            )
        return results

    def fetch(self) -> dict[str, Any]:
        """Fetch account, eligibility and invoices in one round-trip.

        Blocking — call via ``hass.async_add_executor_job``.
        """
        with self._new_session() as session:
            ctx = self._get_aura_context(session)
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
            account, eligibility, invoices = self._call_apex_batch(session, ctx, calls)

        invoices = invoices or []
        return {
            "account": account,
            "eligibility": eligibility,
            "invoices": invoices,
            "latest_invoice": select_latest_invoice(invoices),
        }

    def check_reachable(self) -> None:
        """Lightweight connectivity check for the config flow (blocking)."""
        with self._new_session() as session:
            self._get_aura_context(session)
