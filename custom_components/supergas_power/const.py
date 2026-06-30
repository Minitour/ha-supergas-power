DOMAIN = "supergas_power"
PLATFORMS = ["sensor"]

CONF_LOGISTIC_NUMBER = "logistic_number"
CONF_PHONE = "phone"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"

# The data endpoints are rate-limited per IP, so poll sparingly. Invoices
# change at most once per billing cycle.
DEFAULT_SCAN_INTERVAL_HOURS = 12
MIN_SCAN_INTERVAL_HOURS = 1

# Until the first successful fetch we retry on this (gentler than hammering,
# but sooner than the full interval) so a freshly added integration does not
# sit empty for hours after a transient rate-limit.
COLD_START_RETRY_MINUTES = 30

# Currency display: the new-shekel sign, as requested.
CURRENCY_SYMBOL = "₪"
