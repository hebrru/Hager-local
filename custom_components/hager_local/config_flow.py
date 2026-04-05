"""Config flow for Hager Local."""

from __future__ import annotations

from typing import Any, Mapping
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import (
    HagerApiConnectionError,
    HagerApiError,
    HagerAuthenticationError,
    HagerInteractionRequiredError,
    async_validate_web_credentials,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REAUTH_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATUS_STALE_MINUTES,
    DOMAIN,
    OPTION_SCAN_INTERVAL,
    OPTION_STATUS_STALE_MINUTES,
)

LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _mask_email(value: str) -> str:
    """Return a privacy-friendly representation of an email address."""
    text = value.strip()
    if "@" not in text:
        return "***"

    local_part, _, domain = text.partition("@")
    if not local_part:
        return f"***@{domain}"
    if len(local_part) == 1:
        return f"{local_part}***@{domain}"
    return f"{local_part[:2]}***@{domain}"


def _map_validation_error(err: Exception) -> str:
    """Map a validation exception to the best Home Assistant form error."""
    if isinstance(err, HagerInteractionRequiredError):
        return "interaction_required"
    if isinstance(err, HagerApiConnectionError):
        return "cannot_connect"
    if isinstance(err, HagerAuthenticationError):
        message = str(err).casefold()
        if "email/password combination" in message or "access token was rejected" in message:
            return "invalid_auth"
        return "unknown"
    if isinstance(err, HagerApiError):
        return "unknown"
    return "unknown"


def _build_credentials_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the full credentials schema."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=values.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): PASSWORD_SELECTOR,
        }
    )


def _build_password_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the password-only schema used for reauthentication."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): PASSWORD_SELECTOR,
        }
    )


class HagerLocalConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Hager Local."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            normalized = self._normalize_credentials(user_input)

            try:
                validated = await async_validate_web_credentials(
                    self.hass,
                    normalized[CONF_EMAIL],
                    normalized[CONF_PASSWORD],
                )
            except (
                HagerInteractionRequiredError,
                HagerAuthenticationError,
                HagerApiConnectionError,
                HagerApiError,
            ) as err:
                LOGGER.warning(
                    "Hager credential validation failed for %s: %s: %s",
                    _mask_email(normalized[CONF_EMAIL]),
                    type(err).__name__,
                    err,
                )
                errors["base"] = _map_validation_error(err)
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected error while validating Hager credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(validated["account_id"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(validated["title"]),
                    data={
                        CONF_EMAIL: normalized[CONF_EMAIL],
                        CONF_PASSWORD: normalized[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: str(validated[CONF_ACCESS_TOKEN]),
                        CONF_REAUTH_TOKEN: str(validated[CONF_REAUTH_TOKEN]),
                    },
                )

            user_input = normalized

        return self.async_show_form(
            step_id="user",
            data_schema=_build_credentials_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with the account password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            password = str(user_input[CONF_PASSWORD])
            email = str(entry.data[CONF_EMAIL]).strip()

            try:
                validated = await async_validate_web_credentials(self.hass, email, password)
            except (
                HagerInteractionRequiredError,
                HagerAuthenticationError,
                HagerApiConnectionError,
                HagerApiError,
            ) as err:
                LOGGER.warning(
                    "Hager reauthentication failed for %s: %s: %s",
                    _mask_email(email),
                    type(err).__name__,
                    err,
                )
                errors["base"] = _map_validation_error(err)
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected error while reauthenticating Hager credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(entry.unique_id or email.casefold())
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_ACCESS_TOKEN: str(validated[CONF_ACCESS_TOKEN]),
                        CONF_REAUTH_TOKEN: str(validated[CONF_REAUTH_TOKEN]),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_build_password_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the stored myHager credentials."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            normalized = self._normalize_credentials(user_input)

            try:
                validated = await async_validate_web_credentials(
                    self.hass,
                    normalized[CONF_EMAIL],
                    normalized[CONF_PASSWORD],
                )
            except (
                HagerInteractionRequiredError,
                HagerAuthenticationError,
                HagerApiConnectionError,
                HagerApiError,
            ) as err:
                LOGGER.warning(
                    "Hager reconfiguration validation failed for %s: %s: %s",
                    _mask_email(normalized[CONF_EMAIL]),
                    type(err).__name__,
                    err,
                )
                errors["base"] = _map_validation_error(err)
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected error while reconfiguring Hager credentials")
                errors["base"] = "unknown"
            else:
                existing_account_id = entry.unique_id or str(entry.data.get(CONF_EMAIL, "")).casefold()
                if str(validated["account_id"]) != existing_account_id:
                    errors["base"] = "different_account"
                else:
                    await self.async_set_unique_id(existing_account_id)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_EMAIL: normalized[CONF_EMAIL],
                            CONF_PASSWORD: normalized[CONF_PASSWORD],
                            CONF_ACCESS_TOKEN: str(validated[CONF_ACCESS_TOKEN]),
                            CONF_REAUTH_TOKEN: str(validated[CONF_REAUTH_TOKEN]),
                        },
                    )

            user_input = normalized

        defaults = {
            CONF_EMAIL: entry.data.get(CONF_EMAIL, ""),
            CONF_PASSWORD: entry.data.get(CONF_PASSWORD, ""),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_credentials_schema(defaults if user_input is None else user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this integration."""
        return HagerLocalOptionsFlowHandler(config_entry)

    @staticmethod
    def _normalize_credentials(user_input: dict[str, Any]) -> dict[str, str]:
        """Normalize credential fields before validation."""
        return {
            CONF_EMAIL: str(user_input[CONF_EMAIL]).strip(),
            CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
        }


class HagerLocalOptionsFlowHandler(OptionsFlow):
    """Options flow for Hager Local."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling and online-status options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPTION_SCAN_INTERVAL,
                        default=self._config_entry.options.get(
                            OPTION_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL,
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
                    vol.Required(
                        OPTION_STATUS_STALE_MINUTES,
                        default=self._config_entry.options.get(
                            OPTION_STATUS_STALE_MINUTES,
                            DEFAULT_STATUS_STALE_MINUTES,
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=180)),
                }
            ),
        )
