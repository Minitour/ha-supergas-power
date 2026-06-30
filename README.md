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

## Polling & rate limits

The upstream data endpoints are **rate-limited per source IP**: once a small
per-window quota is spent, they silently return no data. To stay within that
budget the integration:

- polls slowly (default **every 12 hours** — invoices change at most once per
  billing cycle), and
- sends the whole flow as a **single request** per poll, and
- keeps the **last known value** when it is temporarily throttled, instead of
  dropping the sensor to *unavailable*.

Pick a long update interval and avoid reloading the integration repeatedly. If
the sensor is unavailable right after setup, wait for the next scheduled
update.

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
