DOMAIN = "supergas_power"
PLATFORMS = ["sensor"]

CONF_LOGISTIC_NUMBER = "logistic_number"
CONF_PHONE = "phone"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"

# The data endpoints are rate-limited per IP, so poll sparingly. Invoices
# change at most once per billing cycle.
DEFAULT_SCAN_INTERVAL_HOURS = 12
MIN_SCAN_INTERVAL_HOURS = 1

# Currency display: the new-shekel sign, as requested.
CURRENCY_SYMBOL = "₪"
