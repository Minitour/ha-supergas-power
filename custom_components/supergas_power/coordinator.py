from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_LOGISTIC_NUMBER,
    CONF_PHONE,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .supergas_api import SupergasApiError, SupergasClient, SupergasThrottledError

_LOGGER = logging.getLogger(__name__)


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

        session = async_get_clientsession(hass)
        self._client = SupergasClient(
            session=session,
            logistic=entry.data[CONF_LOGISTIC_NUMBER],
            phone=entry.data[CONF_PHONE],
        )

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=max(1, hours)),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._client.async_fetch()
        except SupergasThrottledError as err:
            # The endpoint silently withholds data once the per-IP quota is
            # spent. Keep the last known value rather than going unavailable.
            if self.data is not None:
                _LOGGER.debug("Supergas throttled (%s); keeping previous data", err)
                return self.data
            raise UpdateFailed(
                "Supergas endpoint returned no data (rate-limited). It will "
                "retry on the next scheduled update."
            ) from err
        except SupergasApiError as err:
            raise UpdateFailed(str(err)) from err
