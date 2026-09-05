from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from miraie_ac import Device as MirAIeDevice, MirAIeHub, ConsumptionPeriodType
from aiohttp import ClientError

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .logger import LOGGER
from .utils import get_last_sunday


CUTOFF_HOUR = 12

class MirAIeEnergySensor(SensorEntity, ABC):
    """Sensor for AC Power Consumption."""
    @property
    @abstractmethod
    def period_type(self) -> ConsumptionPeriodType:
        return None

    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        """Initialize the sensor."""
        self.hub = hub
        self.device = device
        self._attr_name = f"{device.name} {self.period_type.value} Energy"
        self._attr_unique_id = f"sensor.{device.name.lower()}_{device.id}_{self.period_type.value.lower()}_energy"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_suggested_display_precision = 2
        self._attr_native_value = None
        self._poll_tasks = set()
        self._removing = False

    async def async_update(self):
        """Update the sensor state with the latest energy consumption data."""
        now = datetime.now().astimezone()
        cutoff_time = now.replace(hour=CUTOFF_HOUR, minute=0, second=0, microsecond=0)
        try:
            consumption = await asyncio.wait_for(self.get_energy_consumption(), timeout=30)
            if consumption is not None:
                if isinstance(consumption, bool):
                    raise ValueError("Invalid energy value")
                consumption = float(consumption)
                if not math.isfinite(consumption) or consumption < 0:
                    raise ValueError("Invalid energy value")
        except (ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError):
            # Keep the last reading/reset timestamp, but signal it is stale.
            self._attr_available = False
            LOGGER.debug("MirAIe energy update failed; retrying at the next interval")
            return
        self._attr_available = True

        """Consumption figures are updated on the server some time between 7-10 am the next day.
        This skips setting the state to unavailable if the value is None and it's not yet
        past the cutoff time.
        """
        if consumption is None and now <= cutoff_time:
            """Skip update if no new data and it's before the cutoff time."""
            return

        if consumption is None:
            self._attr_available = False
            return
        await self._set_last_reset_time()
        self._attr_native_value = consumption

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._removing = False
        self.async_on_remove(async_track_time_interval(
            self.hass, self._async_poll, timedelta(minutes=30)
        ))

    async def _async_poll(self, now=None):
        if self._removing or self._poll_tasks:
            return
        task = asyncio.current_task()
        self._poll_tasks.add(task)
        self.hub.background_tasks.add(task)
        try:
            await self.async_update()
            if not self._removing:
                self.async_write_ha_state()
        finally:
            self._poll_tasks.discard(task)
            self.hub.background_tasks.discard(task)

    async def async_will_remove_from_hass(self):
        """Finish cancellation before the shared HTTP session is closed."""
        self._removing = True
        tasks = list(self._poll_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return await super().async_will_remove_from_hass()

    @abstractmethod
    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest power consumption data."""
        raise NotImplementedError

    async def _set_last_reset_time(self):
        """Reset only when a reading for a reporting period is available."""
        self._attr_last_reset = self._pending_reset

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.device.id)
            },
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

class MirAIeDailyEnergySensor(MirAIeEnergySensor):
    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.DAILY

    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest daily energy consumption data."""
        yesterday = datetime.today().date() - timedelta(days=1)
        self._pending_reset = datetime.combine(yesterday, datetime.min.time()).astimezone()
        date_string = yesterday.strftime("%d%m%Y")
        LOGGER.debug(f"Fetching {self.period_type.value} energy consumption for device: {self._attr_name}, period: {date_string}")
        consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
        return consumption.get(date_string)

class MirAIeWeeklyEnergySensor(MirAIeEnergySensor):
    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.WEEKLY

    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest weekly energy consumption data."""
        sunday = get_last_sunday()
        self._pending_reset = datetime.combine(sunday, datetime.min.time()).astimezone()
        date_string = sunday.strftime("%d%m%Y")
        LOGGER.debug(f"Fetching {self.period_type.value} energy consumption for device: {self._attr_name}, period: {date_string}")
        consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
        return consumption.get(date_string)

class MirAIeMonthlyEnergySensor(MirAIeEnergySensor):
    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.MONTHLY

    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest monthly energy consumption data."""
        yesterday = datetime.today().date() - timedelta(days=1)
        date_string = yesterday.strftime("%m%Y")
        self._pending_reset = datetime.combine(yesterday.replace(day=1), datetime.min.time()).astimezone()
        LOGGER.debug(f"Fetching {self.period_type.value} energy consumption for device: {self._attr_name}, period: {date_string}")
        consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
        return consumption.get(date_string)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up MirAIe energy sensors from a config entry."""
    hub: MirAIeHub = hass.data[DOMAIN][entry.entry_id]
    sensors = []
    for device in hub.home.devices:
        sensors += [
            MirAIeDailyEnergySensor(hub, device),
            MirAIeWeeklyEnergySensor(hub, device),
            MirAIeMonthlyEnergySensor(hub, device),
        ]
    async_add_entities(sensors, update_before_add=True)  # Register sensors
