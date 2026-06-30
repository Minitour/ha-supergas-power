from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CURRENCY_SYMBOL, DOMAIN
from .coordinator import SupergasCoordinator
from .supergas_api import STATUS_EN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SupergasCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LatestInvoiceAmountSensor(coordinator, entry)], True)


class LatestInvoiceAmountSensor(CoordinatorEntity[SupergasCoordinator], SensorEntity):
    """Amount of the most recent invoice (paid or to pay), in shekels."""

    _attr_has_entity_name = True
    _attr_name = "Latest invoice amount"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_SYMBOL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: SupergasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_latest_invoice_amount"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Supergas Power",
            manufacturer="Supergas Power",
        )

    @property
    def _latest(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return data.get("latest_invoice")

    @property
    def native_value(self) -> float | None:
        invoice = self._latest
        if not invoice:
            return None
        amount = invoice.get("INVOICE_AMOUNT", invoice.get("AMOUNT"))
        try:
            return round(float(amount), 2)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        invoice = self._latest
        if not invoice:
            return {}
        status_he = invoice.get("STATUS")
        balance = invoice.get("MATCH_BALANCE")
        return {
            "invoice_number": _as_int(invoice.get("INVOICE_NUMBER")),
            "status": status_he,
            "status_english": STATUS_EN.get(status_he, status_he),
            "period": invoice.get("INVOICE_FROM_TO_PERIOD"),
            "billing_date": invoice.get("EVENT_DATE"),
            "due_date": invoice.get("PAYMENT_DATE"),
            "actual_payment_date": invoice.get("ACTUAL_PAYMENT_DATE"),
            "outstanding_balance": _as_float(balance),
            "net_amount": _as_float(invoice.get("NET_AMOUNT")),
            "vat_amount": _as_float(invoice.get("VAT_AMOUNT")),
        }


def _as_int(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _as_float(value: Any) -> Any:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value
