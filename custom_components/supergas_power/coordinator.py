from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COLD_START_RETRY_MINUTES,
    CONF_LOGISTIC_NUMBER,
    CONF_PHONE,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .supergas_api import SupergasApiError, SupergasClient, SupergasThrottledError

_LOGGER = logging.getLogger(__name__)

_EMPTY: dict[str, Any] = {
    "account": None,
    "eligibility": None,
    "invoices": [],
    "latest_invoice": None,
}


def _has_data(data: dict[str, Any] | None) -> bool:
    return bool(data and data.get("invoices"))


class SupergasCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the Supergas Power invoice endpoint on a slow cadence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        hours = int(
            entry.options.get(
                CONF_SCAN_INTERVAL_HOURS,
                entry.data.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
            )
        )
        self._full_interval = timedelta(hours=max(1, hours))
        self._cold_retry = timedelta(minutes=COLD_START_RETRY_MINUTES)

        self._client = SupergasClient(
            logistic=entry.data[CONF_LOGISTIC_NUMBER],
            phone=entry.data[CONF_PHONE],
        )

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=self._full_interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        # Deliberately never raises on a throttle/no-data response. Setup must
        # not hard-fail, because HA would then retry rapidly and keep the
        # per-IP rate limit tripped. Instead we load (possibly empty) and back
        # off: a short retry while we have never seen data, the full interval
        # once we do.
        try:
            data = await self.hass.async_add_executor_job(self._client.fetch)
        except SupergasThrottledError as err:
            if _has_data(self.data):
                self.update_interval = self._full_interval
                _LOGGER.debug("Throttled (%s); keeping previous data", err)
                return self.data
            self.update_interval = self._cold_retry
            _LOGGER.warning(
                "Supergas returned no invoice data yet (rate-limited, or the "
                "logistic/phone could not be matched). Will retry in %s.",
                self._cold_retry,
            )
            return _EMPTY
        except SupergasApiError as err:
            # Genuine transport/framework errors are transient — let the
            # coordinator surface them and retry on schedule.
            raise UpdateFailed(str(err)) from err

        self.update_interval = self._full_interval
        return data
