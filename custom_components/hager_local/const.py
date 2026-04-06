"""Constants for the Hager Local integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "hager_local"

PLATFORMS: tuple[str, ...] = ("sensor", "binary_sensor", "switch", "select", "number")

CHARGING_MODE_BOOST = "Boost"
CHARGING_MODE_SOLAR_ONLY = "Solar only"
CHARGING_MODE_SOLAR_MINIMUM = "Solar minimum"
CHARGING_MODE_SOLAR_DELAYED = "Solar delayed"
CHARGING_MODE_OPTIONS: tuple[str, ...] = (
    CHARGING_MODE_BOOST,
    CHARGING_MODE_SOLAR_ONLY,
    CHARGING_MODE_SOLAR_MINIMUM,
    CHARGING_MODE_SOLAR_DELAYED,
)

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REAUTH_TOKEN = "re_auth_token"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_STATUS_STALE_MINUTES = 20

OPTION_SCAN_INTERVAL = "scan_interval"
OPTION_STATUS_STALE_MINUTES = "status_stale_minutes"

FLOW_LOGIN_URL = "https://e3dc.e3dc.com/auth-saml/service-providers/hager/login?app=hager"
E3DC_AUTH_BASE_URL = "https://e3dc.e3dc.com"
INSTALLATIONS_BASE_URL = "https://installations.e3dc.com"

TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
