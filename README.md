# Supergas Power Home Assistant Integration

![GitHub Release](https://img.shields.io/github/v/release/Minitour/ha-supergas-power?style=flat-square)
![GitHub Stars](https://img.shields.io/github/stars/Minitour/ha-supergas-power?style=flat-square)
![GitHub Issues](https://img.shields.io/github/issues/Minitour/ha-supergas-power?style=flat-square)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Unofficial integration, use at your own risk!

This integration surfaces your **Supergas Power** gas billing information in
Home Assistant. It reads the invoices from the company's self-service portal
and exposes the most recent invoice's amount as a sensor, so you can show your
latest gas bill on a dashboard, build automations around it, or get notified
when a new charge appears.

## Installation

1. Ensure that [HACS](https://hacs.xyz/) is installed.
2. Add this repository as a custom repository (category: *Integration*).
3. Search for and install the "Supergas Power" integration.
4. Restart Home Assistant.
5. Configure the `Supergas Power` integration.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Minitour&repository=ha-supergas-power&category=Integration)

Alternatively, install manually by copying the
`custom_components/supergas_power` folder into your Home Assistant
`config/custom_components/` directory and restarting.

## Configuration

Add the integration from **Settings → Devices & Services → Add Integration**
and provide:

1. `Logistic number` - the 9-digit customer code printed on every gas invoice.
2. `Phone number` - the phone number associated with the account.
3. `Update interval (hours)` - how often to poll for new invoices. Defaults to
   **12 hours** (minimum 1 hour).

The update interval can be changed later from the integration's **Configure**
(options) screen.

> Treat your logistic number like a low-grade secret. Anyone who knows it can
> look up your invoice history, so don't paste it into screenshots, public
> repos, or forums.

## Features

The integration talks to the same public Salesforce (Aura) endpoint that backs
the Supergas Power invoice-payment web page. On each poll it issues a single
batched request to fetch the service account, eligibility, and the invoice
list, then derives the most recent invoice from that list.

### Supported Entities

- **Sensor — Latest invoice amount**: the amount of your most recent invoice
  (how much was paid, or how much is due), as a `monetary` value displayed in
  Israeli new shekels (`₪`). The most recent invoice is the one with the latest
  billing date.

  The sensor also exposes these state attributes:

  | Attribute | Description |
  |---|---|
  | `invoice_number` | The invoice's identifier |
  | `status` | Invoice status in Hebrew (e.g. `שולם`, `לתשלום`) |
  | `status_english` | English translation of the status |
  | `period` | Billed period, e.g. `04/26-05/26` |
  | `billing_date` | When the invoice was created |
  | `due_date` | Payment due date |
  | `actual_payment_date` | When it was actually paid (paid invoices only) |
  | `outstanding_balance` | Amount still open (`0` when fully paid) |
  | `net_amount` | Pre-VAT amount |
  | `vat_amount` | VAT portion |

## Bot protection & polling

The upstream endpoint sits behind bot protection that fingerprints the
TLS/HTTP2 handshake: a real browser receives data, while an ordinary Python
HTTP client (`requests`/`aiohttp`) gets an empty (`null`) response. This is
**not** IP rate-limiting and not a request-shape problem.

To get past it the integration uses [`curl_cffi`](https://github.com/lexiforest/curl_cffi)
to **impersonate Chrome's TLS/HTTP2 fingerprint**, which makes the request
look like a genuine browser. `curl_cffi` is declared as a requirement and is
installed automatically by Home Assistant. Prebuilt wheels exist for the
common Home Assistant platforms (x86-64 and aarch64); very old 32-bit ARM
hosts may not have a wheel.

Polling is kept gentle regardless:

- it polls slowly (default **every 12 hours** — invoices change at most once
  per billing cycle), and sends the whole flow as a single batched request;
- setup **never hard-fails**: the integration is added immediately and the
  sensor reports *unknown* until the first successful fetch. While it has never
  seen data it retries roughly every 30 minutes; once it has data it switches
  to your configured interval and keeps the last known value if a later poll
  comes back empty.

Avoid setting a very short interval or reloading the integration repeatedly.

## Notes

- This is an unofficial integration and is not affiliated with or endorsed by
  Supergas Power. Use at your own risk.
- The portal exposes invoice **metadata only** — there is no PDF or document
  download available through this API.
- The endpoint is reverse-engineered and unversioned; Supergas Power may change
  or restrict it at any time, which could break this integration.
- Only ever query your **own** account.

## Repository

[GitHub - ha-supergas-power](https://github.com/Minitour/ha-supergas-power)
