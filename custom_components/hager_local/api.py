"""API client for Hager Flow / witty solar via the web account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import base64
import html as html_module
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from aiohttp import ClientError, ClientSession, CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CHARGING_MODE_BOOST,
    CHARGING_MODE_SOLAR_DELAYED,
    CHARGING_MODE_SOLAR_MINIMUM,
    CHARGING_MODE_SOLAR_ONLY,
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REAUTH_TOKEN,
    E3DC_AUTH_BASE_URL,
    FLOW_LOGIN_URL,
    INSTALLATIONS_BASE_URL,
    TOKEN_REFRESH_MARGIN,
    USER_AGENT,
)

LOGGER = logging.getLogger(__name__)

SUN_MODE_DISABLED = "Disabled"
SUN_MODE_IMMEDIATE = "Immediate"
SUN_MODE_DELAYED = "Delayed"

LOGIN_URL_RE = re.compile(
    r"https://login\.hager\.com/interaction/v2/[^\"' <&]+/login\?client_id=[A-Za-z0-9_-]+"
)
FORM_TAG_RE = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
ANCHOR_HREF_RE = re.compile(r"""<a\b[^>]*href=[\"']([^\"']+)[\"']""", re.IGNORECASE)
META_REFRESH_RE = re.compile(
    r"""<meta\b[^>]*http-equiv=[\"']refresh[\"'][^>]*content=[\"'][^\"']*url=([^\"'>]+)""",
    re.IGNORECASE,
)
JS_REDIRECT_RE = re.compile(
    r"""(?:window\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']""",
    re.IGNORECASE,
)


class HagerApiError(Exception):
    """Base error for Hager API failures."""


class HagerApiConnectionError(HagerApiError):
    """Raised when the API is unreachable."""


class HagerAuthenticationError(HagerApiError):
    """Raised when authentication failed."""


class HagerInteractionRequiredError(HagerAuthenticationError):
    """Raised when the login flow needs browser interaction."""


@dataclass(slots=True)
class _RawCookie:
    """Cookie preserved manually when aiohttp drops the raw value."""

    name: str
    value: str
    domain: str
    path: str


def _decode_token_exp(access_token: str | None) -> float | None:
    """Return the JWT expiration timestamp."""
    if not access_token:
        return None

    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None

        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded.decode("utf-8"))
        exp = data.get("exp")
        if exp is None:
            return None
        return float(exp)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _extract_tokens_from_url(url: str) -> tuple[str | None, str | None]:
    """Extract the token pair from a redirect URL."""
    query = parse_qs(urlparse(url).query)
    access_token = query.get("token", [None])[0]
    reauth_token = query.get("reAuthToken", [None])[0]
    return access_token, reauth_token


def _default_cookie_path(path: str) -> str:
    """Return the RFC default-path for a cookie."""
    if not path or not path.startswith("/"):
        return "/"
    if path == "/":
        return "/"
    if path.endswith("/"):
        return path
    return path.rsplit("/", 1)[0] or "/"


def _store_raw_cookies(
    request_url: str,
    set_cookie_headers: list[str],
    raw_cookies: dict[tuple[str, str, str], _RawCookie],
) -> None:
    """Store raw cookies from response headers without aiohttp normalization."""
    parsed_url = urlparse(request_url)
    default_domain = (parsed_url.hostname or "").casefold()
    default_path = _default_cookie_path(parsed_url.path)

    for header in set_cookie_headers:
        first_part, _, remainder = header.partition(";")
        name, separator, value = first_part.partition("=")
        if not separator:
            continue

        domain = default_domain
        path = default_path
        for attribute in remainder.split(";"):
            attribute = attribute.strip()
            lower_attribute = attribute.casefold()
            if lower_attribute.startswith("domain="):
                domain = attribute[7:].strip().lstrip(".").casefold()
            elif lower_attribute.startswith("path="):
                path = attribute[5:].strip() or "/"

        key = (domain, path, name.strip())
        if value == "":
            raw_cookies.pop(key, None)
            continue

        raw_cookies[key] = _RawCookie(
            name=name.strip(),
            value=value,
            domain=domain,
            path=path,
        )


def _cookie_matches_request(cookie: _RawCookie, request_url: str) -> bool:
    """Return whether the raw cookie should be sent to this request URL."""
    parsed_url = urlparse(request_url)
    hostname = (parsed_url.hostname or "").casefold()
    request_path = parsed_url.path or "/"

    domain_match = hostname == cookie.domain or hostname.endswith(f".{cookie.domain}")
    path_match = request_path.startswith(cookie.path)
    return domain_match and path_match


def _build_cookie_header(
    session: ClientSession,
    request_url: str,
    raw_cookies: dict[tuple[str, str, str], _RawCookie],
) -> str | None:
    """Merge aiohttp cookies with raw cookies preserved manually."""
    cookie_values: dict[str, str] = {}

    for morsel in session.cookie_jar:
        if _cookie_matches_request(
            _RawCookie(
                name=morsel.key,
                value=morsel.value,
                domain=(morsel["domain"] or "").lstrip(".").casefold(),
                path=morsel["path"] or "/",
            ),
            request_url,
        ):
            cookie_values[morsel.key] = morsel.value

    for raw_cookie in raw_cookies.values():
        if _cookie_matches_request(raw_cookie, request_url):
            cookie_values[raw_cookie.name] = raw_cookie.value

    if not cookie_values:
        return None

    return "; ".join(f"{name}={value}" for name, value in cookie_values.items())


def _extract_login_url(html: str) -> str:
    """Extract the login form URL from the myHager HTML page."""
    decoded_html = html_module.unescape(html)
    match = LOGIN_URL_RE.search(decoded_html)
    if not match:
        raise HagerAuthenticationError(
            "Unable to locate the Hager login form in the web authentication page"
        )
    return match.group(0)


def _extract_html_attribute(tag: str, attribute: str) -> str | None:
    """Extract a single HTML attribute from a tag snippet."""
    match = re.search(
        rf"""\b{re.escape(attribute)}\s*=\s*[\"']([^\"']*)[\"']""",
        tag,
        re.IGNORECASE,
    )
    if not match:
        return None
    return html_module.unescape(match.group(1))


def _extract_auto_post_form(html: str) -> tuple[str, dict[str, str]] | None:
    """Extract a POST form and its named fields from an HTML page."""
    decoded_html = html_module.unescape(html)

    form_tag_match = FORM_TAG_RE.search(decoded_html)
    if not form_tag_match:
        return None

    form_tag = form_tag_match.group(0)
    method = (_extract_html_attribute(form_tag, "method") or "get").casefold()
    action = _extract_html_attribute(form_tag, "action")
    if method != "post" or not action:
        return None

    fields: dict[str, str] = {}
    for input_tag_match in INPUT_TAG_RE.finditer(decoded_html):
        input_tag = input_tag_match.group(0)
        input_name = _extract_html_attribute(input_tag, "name")
        if not input_name:
            continue

        input_type = (_extract_html_attribute(input_tag, "type") or "text").casefold()
        if input_type in {"submit", "button", "image", "reset"}:
            continue

        fields[input_name] = _extract_html_attribute(input_tag, "value") or ""

    return action, fields


def _extract_html_redirect_url(html: str) -> str | None:
    """Extract a redirect target embedded in an HTML response body."""
    decoded_html = html_module.unescape(html)
    for pattern in (META_REFRESH_RE, JS_REDIRECT_RE, ANCHOR_HREF_RE):
        match = pattern.search(decoded_html)
        if match:
            return html_module.unescape(match.group(1))
    return None


def _normalize_record_table(payload: Any) -> list[dict[str, Any]]:
    """Normalize either a list of objects or a column-oriented object of arrays."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    list_lengths = [len(value) for value in payload.values() if isinstance(value, list)]
    if not list_lengths:
        return [payload]

    record_count = max(list_lengths)
    records: list[dict[str, Any]] = []
    for index in range(record_count):
        record: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, list):
                record[key] = value[index] if index < len(value) else None
            else:
                record[key] = value
        records.append(record)

    return records


def _build_http_error_message(status: int, url: str, body: str) -> str:
    """Convert an HTTP error response into a concise Hager exception message."""
    detail_parts: list[str] = []

    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            error = payload.get("error")
            message = payload.get("message")
            if isinstance(error, str) and error.strip():
                detail_parts.append(error.strip())
            if isinstance(message, str) and message.strip():
                detail_parts.append(message.strip())
        elif body.strip():
            detail_parts.append(body.strip())

    details = " - ".join(dict.fromkeys(detail_parts))
    if details:
        return f"Hager API request failed with HTTP {status}: {details}"
    return f"Hager API request failed with HTTP {status} at {url}"


def _normalize_charging_mode(value: str) -> str:
    """Normalize a user-facing or legacy charge mode value."""
    aliases = {
        CHARGING_MODE_BOOST.casefold(): CHARGING_MODE_BOOST,
        "standard": CHARGING_MODE_BOOST,
        CHARGING_MODE_SOLAR_ONLY.casefold(): CHARGING_MODE_SOLAR_ONLY,
        "pv only": CHARGING_MODE_SOLAR_ONLY,
        "pvonly": CHARGING_MODE_SOLAR_ONLY,
        SUN_MODE_DISABLED.casefold(): CHARGING_MODE_SOLAR_ONLY,
        CHARGING_MODE_SOLAR_MINIMUM.casefold(): CHARGING_MODE_SOLAR_MINIMUM,
        "pv immediate": CHARGING_MODE_SOLAR_MINIMUM,
        "pvimmediate": CHARGING_MODE_SOLAR_MINIMUM,
        SUN_MODE_IMMEDIATE.casefold(): CHARGING_MODE_SOLAR_MINIMUM,
        CHARGING_MODE_SOLAR_DELAYED.casefold(): CHARGING_MODE_SOLAR_DELAYED,
        "pv delayed": CHARGING_MODE_SOLAR_DELAYED,
        "pvdelayed": CHARGING_MODE_SOLAR_DELAYED,
        SUN_MODE_DELAYED.casefold(): CHARGING_MODE_SOLAR_DELAYED,
    }

    normalized = value.strip().casefold()
    if normalized not in aliases:
        raise HagerApiError(f"Unsupported Hager charging mode: {value}")
    return aliases[normalized]


def _charging_mode_from_parameters(parameters: dict[str, Any]) -> str | None:
    """Map the raw Hager sun mode payload to a stable charge mode label."""
    sun_mode = parameters.get("sunMode") or {}
    if not isinstance(sun_mode, dict):
        return None

    activated = sun_mode.get("activated")
    strategy = sun_mode.get("chargingStrategy")
    normalized_strategy = strategy.casefold() if isinstance(strategy, str) else None

    if activated is False:
        return CHARGING_MODE_BOOST
    if normalized_strategy == SUN_MODE_DISABLED.casefold():
        return CHARGING_MODE_SOLAR_ONLY
    if normalized_strategy == SUN_MODE_IMMEDIATE.casefold():
        return CHARGING_MODE_SOLAR_MINIMUM
    if normalized_strategy == SUN_MODE_DELAYED.casefold():
        return CHARGING_MODE_SOLAR_DELAYED
    return None


def _build_sun_mode_payload(current_parameters: dict[str, Any], charging_mode: str) -> dict[str, Any]:
    """Return the updated sun mode payload for a requested charge mode."""
    sun_mode = dict(current_parameters.get("sunMode") or {})
    normalized_mode = _normalize_charging_mode(charging_mode)

    if normalized_mode == CHARGING_MODE_BOOST:
        sun_mode["activated"] = False
        sun_mode["chargingStrategy"] = SUN_MODE_DISABLED
        return sun_mode

    sun_mode["activated"] = True
    if normalized_mode == CHARGING_MODE_SOLAR_ONLY:
        sun_mode["chargingStrategy"] = SUN_MODE_DISABLED
    elif normalized_mode == CHARGING_MODE_SOLAR_MINIMUM:
        sun_mode["chargingStrategy"] = SUN_MODE_IMMEDIATE
    else:
        sun_mode["chargingStrategy"] = SUN_MODE_DELAYED
    return sun_mode


def _configuration_id_from_evse(evse: dict[str, Any]) -> str:
    """Return the wallbox identifier used by the live configuration endpoint."""
    media_parameters = evse.get("mediaParameters") or {}
    local_id = media_parameters.get("localId")
    if local_id is not None:
        return str(local_id)

    evse_sub_type_parameters = evse.get("evseSubTypeParameters") or {}
    wallbox_id = evse_sub_type_parameters.get("wallboxId")
    if wallbox_id is not None:
        return str(wallbox_id)

    return str(evse.get("id"))


async def async_login_with_password(email: str, password: str) -> dict[str, Any]:
    """Authenticate against the myHager web login and return bearer tokens."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": USER_AGENT,
    }

    try:
        async with ClientSession(
            headers=headers,
            cookie_jar=CookieJar(unsafe=True, quote_cookie=False),
        ) as session:
            raw_cookies: dict[tuple[str, str, str], _RawCookie] = {}
            next_url = FLOW_LOGIN_URL
            login_page = ""
            final_url = FLOW_LOGIN_URL

            for _ in range(10):
                request_headers: dict[str, str] = {}
                cookie_header = _build_cookie_header(session, next_url, raw_cookies)
                if cookie_header:
                    request_headers["Cookie"] = cookie_header

                async with session.get(
                    next_url,
                    headers=request_headers or None,
                    allow_redirects=False,
                ) as response:
                    login_page = await response.text()
                    final_url = str(response.url)
                    _store_raw_cookies(
                        final_url,
                        response.headers.getall("Set-Cookie", []),
                        raw_cookies,
                    )

                    location = response.headers.get("Location")
                    if response.status in (301, 302, 303, 307, 308) and location:
                        next_url = urljoin(final_url, location)
                        continue

                    break

            parsed_final_url = urlparse(final_url)
            if "/interaction/" in parsed_final_url.path and "/login" in parsed_final_url.path:
                login_url = final_url
            else:
                login_url = _extract_login_url(login_page)

            post_headers = {
                "Origin": "https://login.hager.com",
                "Referer": login_url,
            }
            cookie_header = _build_cookie_header(session, login_url, raw_cookies)
            if cookie_header:
                post_headers["Cookie"] = cookie_header

            async with session.post(
                login_url,
                data={"email": email, "password": password},
                headers=post_headers,
                allow_redirects=False,
            ) as response:
                _store_raw_cookies(
                    str(response.url),
                    response.headers.getall("Set-Cookie", []),
                    raw_cookies,
                )
                location = response.headers.get("Location")
                if response.status not in (302, 303) or not location:
                    raise HagerAuthenticationError("Unexpected response from the Hager login form")
                if "/interaction/" in location and "/login" in location:
                    raise HagerAuthenticationError("Hager rejected the email/password combination")
                next_url = urljoin(str(response.url), location)

            pending_html: tuple[str, str] | None = None
            for _ in range(20):
                access_token, reauth_token = _extract_tokens_from_url(next_url)
                if access_token and reauth_token:
                    return {
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REAUTH_TOKEN: reauth_token,
                    }

                if pending_html is None:
                    parsed = urlparse(next_url)
                    if "/interaction/" in parsed.path and "/consent" in parsed.path:
                        raise HagerInteractionRequiredError(
                            "Hager requires a browser consent confirmation before Home Assistant can sign in"
                        )
                    if "/interaction/" in parsed.path and "/mfa/" in parsed.path:
                        raise HagerInteractionRequiredError(
                            "Hager requires an interactive MFA step that this integration cannot complete automatically"
                        )

                    request_headers = {}
                    cookie_header = _build_cookie_header(session, next_url, raw_cookies)
                    if cookie_header:
                        request_headers["Cookie"] = cookie_header

                    async with session.get(
                        next_url,
                        headers=request_headers or None,
                        allow_redirects=False,
                    ) as response:
                        _store_raw_cookies(
                            str(response.url),
                            response.headers.getall("Set-Cookie", []),
                            raw_cookies,
                        )
                        location = response.headers.get("Location")
                        if response.status in (301, 302, 303, 307, 308) and location:
                            next_url = urljoin(str(response.url), location)
                            continue
                        current_url = str(response.url)
                        body = await response.text()
                else:
                    current_url, body = pending_html
                    pending_html = None

                if "This temporary URL has expired" in body:
                    raise HagerAuthenticationError("The temporary Hager login URL expired")

                auto_post_form = _extract_auto_post_form(body)
                if auto_post_form is not None:
                    form_action, form_fields = auto_post_form
                    form_url = urljoin(current_url, form_action)
                    request_headers = {}
                    cookie_header = _build_cookie_header(session, form_url, raw_cookies)
                    if cookie_header:
                        request_headers["Cookie"] = cookie_header

                    async with session.post(
                        form_url,
                        data=form_fields,
                        headers=request_headers or None,
                        allow_redirects=False,
                    ) as response:
                        _store_raw_cookies(
                            str(response.url),
                            response.headers.getall("Set-Cookie", []),
                            raw_cookies,
                        )
                        access_token, reauth_token = _extract_tokens_from_url(str(response.url))
                        if access_token and reauth_token:
                            return {
                                CONF_ACCESS_TOKEN: access_token,
                                CONF_REAUTH_TOKEN: reauth_token,
                            }

                        location = response.headers.get("Location")
                        if response.status in (301, 302, 303, 307, 308) and location:
                            next_url = urljoin(str(response.url), location)
                            continue

                        pending_html = (str(response.url), await response.text())
                        continue

                html_redirect_url = _extract_html_redirect_url(body)
                if html_redirect_url:
                    next_url = urljoin(current_url, html_redirect_url)
                    continue

                raise HagerAuthenticationError(
                    "Unexpected Hager login response while waiting for bearer tokens "
                    f"at {urlparse(current_url).netloc}{urlparse(current_url).path}"
                )
    except HagerApiError:
        raise
    except ClientError as err:
        raise HagerApiConnectionError("Unable to reach the Hager web login") from err

    raise HagerAuthenticationError("The Hager login flow did not return any tokens")


async def async_validate_web_credentials(
    hass: HomeAssistant,
    email: str,
    password: str,
) -> dict[str, Any]:
    """Validate myHager credentials and return the initial token payload."""
    tokens = await async_login_with_password(email, password)
    session = async_get_clientsession(hass)
    access_token = str(tokens[CONF_ACCESS_TOKEN])

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT,
    }

    try:
        async with session.get(
            f"{INSTALLATIONS_BASE_URL}/installations",
            headers=headers,
        ) as response:
            if response.status == 401:
                raise HagerAuthenticationError("The Hager access token was rejected")
            response.raise_for_status()
            payload = await response.json(content_type=None)
    except ClientError as err:
        raise HagerApiConnectionError("Unable to reach the Hager installations API") from err

    if not _normalize_record_table(payload):
        raise HagerApiError("Unexpected response shape from the Hager installations API")

    return {
        "account_id": email.strip().lower(),
        "title": email.strip(),
        **tokens,
    }


@dataclass(slots=True)
class HagerEmcSnapshot:
    """Normalized Flow EMC payload."""

    installation: dict[str, Any]
    emc_device_link: dict[str, Any]
    sub_devices: dict[str, Any]
    overview: dict[str, Any] | None

    @property
    def installation_id(self) -> str:
        return str(self.installation.get("id"))

    @property
    def installation_name(self) -> str:
        return str(
            self.installation.get("projectName")
            or self.installation.get("name")
            or f"Installation {self.installation_id}"
        )

    @property
    def emc_link_id(self) -> str:
        return str(self.emc_device_link.get("id"))

    @property
    def device_id(self) -> str:
        candidate = self.emc_device_link.get("deviceId") or self.installation.get("emsMasterDeviceId")
        return str(candidate or self.emc_link_id)

    @property
    def short_id(self) -> str | None:
        candidate = self.emc_device_link.get("deviceShortUuid") or self.installation.get(
            "emsMasterDeviceShortUuid"
        )
        return str(candidate) if candidate else None

    @property
    def product_name(self) -> str | None:
        candidate = self.emc_device_link.get("productName") or self.installation.get("emsMasterProduct")
        return str(candidate) if candidate else None

    @property
    def display_name(self) -> str:
        preferred = self.emc_device_link.get("deviceName")
        if preferred:
            return str(preferred)
        if self.short_id:
            return f"Flow EMC {self.short_id[-6:]}"
        return "Flow EMC"

    @property
    def serial_number(self) -> str | None:
        return self.short_id or self.device_id

    @property
    def device_status(self) -> str | None:
        status = self.emc_device_link.get("deviceStatus") or self.emc_device_link.get(
            "lastKnownDeviceStatus"
        )
        return str(status) if status else None

    @property
    def installation_status(self) -> str | None:
        status = self.installation.get("installationStatus")
        return str(status) if status else None

    @property
    def last_status_timestamp(self) -> str | None:
        timestamp = self.emc_device_link.get("lastKnownDeviceStatusTimestamp") or self.installation.get(
            "lastKnownDeviceStatusTimestamp"
        )
        return str(timestamp) if timestamp else None

    @property
    def properties(self) -> dict[str, Any]:
        return self.overview or {}

    @property
    def monitoring(self) -> dict[str, Any]:
        monitoring = self.sub_devices.get("monitoring") or {}
        return monitoring if isinstance(monitoring, dict) else {}

    @property
    def meter_count(self) -> int:
        meters = self.monitoring.get("meters") or []
        return len(meters) if isinstance(meters, list) else 0

    @property
    def controlled_device_count(self) -> int:
        controlled = self.sub_devices.get("controlled") or []
        return len(controlled) if isinstance(controlled, list) else 0

    @property
    def storage_count(self) -> int:
        storage = self.sub_devices.get("qntmStorage") or []
        return len(storage) if isinstance(storage, list) else 0

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.installation_name.casefold(), self.display_name.casefold())


@dataclass(slots=True)
class HagerMeterSnapshot:
    """Normalized monitoring meter payload."""

    installation: dict[str, Any]
    emc_device_link: dict[str, Any]
    meter: dict[str, Any]
    overview: dict[str, Any] | None
    meter_group_size: int

    @property
    def installation_id(self) -> str:
        return str(self.installation.get("id"))

    @property
    def installation_name(self) -> str:
        return str(
            self.installation.get("projectName")
            or self.installation.get("name")
            or f"Installation {self.installation_id}"
        )

    @property
    def emc_device_id(self) -> str:
        candidate = self.emc_device_link.get("deviceId") or self.installation.get("emsMasterDeviceId")
        return str(candidate or self.meter_id)

    @property
    def meter_id(self) -> str:
        return str(self.meter.get("id"))

    @property
    def device_id(self) -> str:
        candidate = self.meter.get("deviceId") or self.meter.get("deviceUuid")
        if candidate:
            return str(candidate)
        return f"{self.emc_device_id}_meter_{self.meter_id}"

    @property
    def display_name(self) -> str:
        preferred = self.meter.get("deviceName")
        if preferred:
            return str(preferred)
        if self.device_type == "PVExtern":
            return "Compteur photovoltaique"
        return f"Compteur {self.meter_id}"

    @property
    def device_type(self) -> str | None:
        value = self.meter.get("deviceType")
        return str(value) if value else None

    @property
    def media(self) -> str | None:
        value = self.meter.get("media")
        return str(value) if value else None

    @property
    def media_parameters(self) -> dict[str, Any]:
        return self.meter.get("mediaParameters") or {}

    @property
    def type_parameters(self) -> dict[str, Any]:
        return self.meter.get("typeParameters") or {}

    @property
    def properties(self) -> dict[str, Any]:
        return self.overview or {}

    @property
    def device_status(self) -> str | None:
        status = self.meter.get("deviceStatus") or self.meter.get("lastKnownDeviceStatus")
        return str(status) if status else None

    @property
    def last_status_timestamp(self) -> str | None:
        timestamp = self.meter.get("lastKnownDeviceStatusTimestamp") or self.meter.get("updatedAt")
        return str(timestamp) if timestamp else None

    @property
    def status_code(self) -> int | str | None:
        return self.meter.get("deviceStatusCode")

    @property
    def wiring_mode(self) -> str | None:
        value = self.meter.get("wiringMode")
        return str(value) if value else None

    @property
    def modbus_address(self) -> int | str | None:
        return self.media_parameters.get("address")

    @property
    def peak_power(self) -> float | int | None:
        return self.type_parameters.get("peakPower")

    @property
    def current_power(self) -> float | int | None:
        if self.device_type != "PVExtern" or self.meter_group_size != 1:
            return None
        overview = self.properties.get("wallboxCurrentOverview") or {}
        if not isinstance(overview, dict):
            return None
        return overview.get("SUN")

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.installation_name.casefold(),
            self.display_name.casefold(),
            self.meter_id.casefold(),
        )


@dataclass(slots=True)
class HagerWallboxSnapshot:
    """Normalized wallbox payload."""

    installation: dict[str, Any]
    emc_device_link: dict[str, Any]
    evse: dict[str, Any]
    configuration: dict[str, Any] | None

    @property
    def installation_id(self) -> str:
        return str(self.installation.get("id"))

    @property
    def installation_name(self) -> str:
        return str(
            self.installation.get("projectName")
            or self.installation.get("name")
            or f"Installation {self.installation_id}"
        )

    @property
    def emc_link_id(self) -> str:
        return str(self.emc_device_link.get("id"))

    @property
    def emc_hardware_id(self) -> str:
        return str(self.emc_device_link.get("deviceId"))

    @property
    def evse_id(self) -> str:
        return str(self.evse.get("id"))

    @property
    def wallbox_id(self) -> str:
        return str((self.evse.get("evseSubTypeParameters") or {}).get("wallboxId") or self.evse_id)

    @property
    def configuration_id(self) -> str:
        local_id = self.media_parameters.get("localId")
        if local_id is not None:
            return str(local_id)
        return self.wallbox_id

    @property
    def media(self) -> str:
        return str(self.evse.get("media") or "")

    @property
    def media_parameters(self) -> dict[str, Any]:
        return self.evse.get("mediaParameters") or {}

    @property
    def evse_parameters(self) -> dict[str, Any]:
        return self.evse.get("parameters") or {}

    @property
    def charging_mode(self) -> str | None:
        return _charging_mode_from_parameters(self.evse_parameters)

    @property
    def properties(self) -> dict[str, Any]:
        return self.configuration or {}

    @property
    def serial_number(self) -> str | None:
        candidates = (
            self.properties.get("serial"),
            self.media_parameters.get("serialNumber"),
            self.media_parameters.get("macAddress"),
            self.wallbox_id,
        )
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    @property
    def device_reference(self) -> str | None:
        reference = (self.evse.get("evseSubTypeParameters") or {}).get("deviceReference")
        return str(reference) if reference else None

    @property
    def device_id(self) -> str:
        candidates = (
            self.media_parameters.get("macAddress"),
            self.wallbox_id,
            self.properties.get("serial"),
            self.evse_id,
        )
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return self.evse_id

    @property
    def display_name(self) -> str:
        preferred = self.evse.get("deviceName") or self.properties.get("deviceName")
        if preferred:
            return str(preferred)
        if self.serial_number:
            return f"Hager Wallbox {self.serial_number[-6:]}"
        return f"Hager Wallbox {self.evse_id}"

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.installation_name.casefold(),
            self.display_name.casefold(),
            (self.serial_number or self.evse_id).casefold(),
        )


@dataclass(slots=True)
class HagerAccountSnapshot:
    """Normalized account-wide payload."""

    account_id: str
    account_email: str | None
    installations: dict[str, dict[str, Any]]
    emcs: dict[str, HagerEmcSnapshot]
    meters: dict[str, HagerMeterSnapshot]
    wallboxes: dict[str, HagerWallboxSnapshot]
    fetched_at: datetime


class HagerApiClient:
    """Handle web authentication and Flow / E3DC API requests for Hager."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._session = async_get_clientsession(hass)
        self._refresh_lock = asyncio.Lock()

    @property
    def email(self) -> str:
        return str(self._entry.data[CONF_EMAIL])

    @property
    def password(self) -> str:
        return str(self._entry.data[CONF_PASSWORD])

    @property
    def access_token(self) -> str | None:
        token = self._entry.data.get(CONF_ACCESS_TOKEN)
        return str(token) if token else None

    @property
    def reauth_token(self) -> str | None:
        token = self._entry.data.get(CONF_REAUTH_TOKEN)
        return str(token) if token else None

    async def async_validate_connection(self) -> HagerAccountSnapshot:
        return await self.async_get_overview()

    async def async_get_overview(self) -> HagerAccountSnapshot:
        installations = await self._get_installations()
        installation_map = {str(item["id"]): item for item in installations}

        device_links_results = await asyncio.gather(
            *[self._get_device_links(str(installation["id"])) for installation in installations]
        )

        sub_device_requests: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for installation, device_links in zip(installations, device_links_results, strict=True):
            active_emc = self._select_active_emc(device_links)
            if active_emc:
                sub_device_requests.append((installation, active_emc))

        sub_devices_results = await asyncio.gather(
            *[
                self._get_sub_devices(str(installation["id"]), str(emc["id"]))
                for installation, emc in sub_device_requests
            ]
        )

        emcs: list[HagerEmcSnapshot] = []
        meters: list[HagerMeterSnapshot] = []
        wallboxes: list[HagerWallboxSnapshot] = []

        for (installation, emc), sub_devices in zip(sub_device_requests, sub_devices_results, strict=True):
            monitoring = sub_devices.get("monitoring") or {}
            meter_devices = monitoring.get("meters") if isinstance(monitoring, dict) else []
            if not isinstance(meter_devices, list):
                meter_devices = []

            controlled_devices = sub_devices.get("controlled") or []
            evses = [
                device
                for device in controlled_devices
                if isinstance(device, dict) and device.get("type") == "Evse"
            ]

            configurations = await asyncio.gather(
                *[
                    self._get_wallbox_configuration(
                        str(emc.get("deviceId")),
                        _configuration_id_from_evse(evse),
                    )
                    for evse in evses
                ]
            )

            live_overview = next(
                (configuration for configuration in configurations if isinstance(configuration, dict)),
                None,
            )

            emcs.append(
                HagerEmcSnapshot(
                    installation=installation,
                    emc_device_link=emc,
                    sub_devices=sub_devices,
                    overview=live_overview,
                )
            )

            for meter in meter_devices:
                if not isinstance(meter, dict):
                    continue
                meters.append(
                    HagerMeterSnapshot(
                        installation=installation,
                        emc_device_link=emc,
                        meter=meter,
                        overview=live_overview,
                        meter_group_size=len(meter_devices),
                    )
                )

            for evse, configuration in zip(evses, configurations, strict=True):
                wallboxes.append(
                    HagerWallboxSnapshot(
                        installation=installation,
                        emc_device_link=emc,
                        evse=evse,
                        configuration=configuration if isinstance(configuration, dict) else None,
                    )
                )

        emcs.sort(key=lambda item: item.sort_key)
        meters.sort(key=lambda item: item.sort_key)
        wallboxes.sort(key=lambda item: item.sort_key)

        return HagerAccountSnapshot(
            account_id=self.email.strip().lower(),
            account_email=self.email,
            installations=installation_map,
            emcs={item.device_id: item for item in emcs},
            meters={item.device_id: item for item in meters},
            wallboxes={item.device_id: item for item in wallboxes},
            fetched_at=datetime.now(UTC),
        )

    async def async_set_charging_mode(self, wallbox: HagerWallboxSnapshot, charging_mode: str) -> None:
        payload = self._build_evse_update_payload(wallbox, charging_mode)
        await self._request_json(
            "put",
            (
                f"{INSTALLATIONS_BASE_URL}/installations/{wallbox.installation_id}"
                f"/device-links/{wallbox.emc_link_id}/sub/controlled/{wallbox.evse_id}"
            ),
            json_payload=payload,
            expect_json=False,
        )

    async def async_set_boost_mode(self, wallbox: HagerWallboxSnapshot, enabled: bool) -> None:
        await self._request_json(
            "put",
            f"{E3DC_AUTH_BASE_URL}/wallboxes/{wallbox.emc_hardware_id}/{wallbox.configuration_id}/configuration",
            json_payload={"chargeFull": enabled},
            expect_json=False,
        )

    async def async_set_charge_strategy(self, wallbox: HagerWallboxSnapshot, charging_strategy: str) -> None:
        await self.async_set_charging_mode(wallbox, charging_strategy)

    async def async_get_access_token(self) -> str:
        access_token = self.access_token
        expires_at = _decode_token_exp(access_token)
        if access_token and expires_at is not None:
            expiration = datetime.fromtimestamp(expires_at, UTC)
            if expiration - TOKEN_REFRESH_MARGIN > datetime.now(UTC):
                return access_token
        return await self.async_refresh_access_token()

    async def async_refresh_access_token(self) -> str:
        async with self._refresh_lock:
            access_token = self.access_token
            expires_at = _decode_token_exp(access_token)
            if access_token and expires_at is not None:
                expiration = datetime.fromtimestamp(expires_at, UTC)
                if expiration - TOKEN_REFRESH_MARGIN > datetime.now(UTC):
                    return access_token

            reauth_token = self.reauth_token
            if reauth_token:
                headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
                try:
                    async with self._session.post(
                        f"{E3DC_AUTH_BASE_URL}/auth-saml/re-auth",
                        headers=headers,
                        json={CONF_REAUTH_TOKEN: reauth_token},
                    ) as response:
                        if response.status == 401:
                            raise HagerAuthenticationError("Hager rejected the re-auth token")
                        if response.status == 400:
                            raise HagerAuthenticationError("Hager rejected the re-auth request")
                        response.raise_for_status()
                        payload = await response.json(content_type=None)
                except HagerAuthenticationError:
                    payload = None
                except ClientError as err:
                    raise HagerApiConnectionError("Unable to refresh the Hager access token") from err

                if isinstance(payload, dict):
                    new_access_token = payload.get("token")
                    new_reauth_token = payload.get("reAuthToken")
                    if new_access_token and new_reauth_token:
                        self._persist_tokens(str(new_access_token), str(new_reauth_token))
                        return str(new_access_token)

            payload = await async_login_with_password(self.email, self.password)
            access_token = str(payload[CONF_ACCESS_TOKEN])
            reauth_token = str(payload[CONF_REAUTH_TOKEN])
            self._persist_tokens(access_token, reauth_token)
            return access_token

    async def _get_installations(self) -> list[dict[str, Any]]:
        payload = await self._request_json("get", f"{INSTALLATIONS_BASE_URL}/installations")
        installations = _normalize_record_table(payload)
        if not installations:
            raise HagerApiError("Unexpected response shape from the Hager installations API")
        return installations

    async def _get_device_links(self, installation_id: str) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "get",
            f"{INSTALLATIONS_BASE_URL}/installations/{installation_id}/device-links",
        )
        return _normalize_record_table(payload)

    async def _get_sub_devices(self, installation_id: str, emc_link_id: str) -> dict[str, Any]:
        payload = await self._request_json(
            "get",
            f"{INSTALLATIONS_BASE_URL}/installations/{installation_id}/device-links/{emc_link_id}/sub",
        )
        if not isinstance(payload, dict):
            raise HagerApiError("Unexpected response shape from the Hager sub-devices API")
        return payload

    async def _get_wallbox_configuration(self, emc_hardware_id: str, wallbox_id: str) -> dict[str, Any] | None:
        access_token = await self.async_get_access_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        }
        url = f"{E3DC_AUTH_BASE_URL}/wallboxes/{emc_hardware_id}/{wallbox_id}/configuration"

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (400, 403, 404):
                    LOGGER.debug(
                        "Skipping live wallbox configuration for %s/%s due to HTTP %s",
                        emc_hardware_id,
                        wallbox_id,
                        response.status,
                    )
                    await response.read()
                    return None

                if response.status == 401:
                    access_token = await self.async_refresh_access_token()
                    headers["Authorization"] = f"Bearer {access_token}"
                    async with self._session.get(url, headers=headers) as retry_response:
                        if retry_response.status in (400, 403, 404):
                            LOGGER.debug(
                                "Skipping live wallbox configuration for %s/%s after refresh due to HTTP %s",
                                emc_hardware_id,
                                wallbox_id,
                                retry_response.status,
                            )
                            await retry_response.read()
                            return None
                        if retry_response.status >= 400:
                            body = await retry_response.text()
                            raise HagerApiError(
                                _build_http_error_message(retry_response.status, url, body)
                            )
                        payload = await retry_response.json(content_type=None)
                else:
                    if response.status >= 400:
                        body = await response.text()
                        raise HagerApiError(_build_http_error_message(response.status, url, body))
                    payload = await response.json(content_type=None)
        except ClientError as err:
            raise HagerApiConnectionError(
                f"Unable to reach the Hager wallbox configuration API for {wallbox_id}"
            ) from err

        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise HagerApiError("Unexpected response shape from the Hager wallbox API")
        return payload

    @staticmethod
    def _select_active_emc(device_links: list[dict[str, Any]]) -> dict[str, Any] | None:
        active_links = [
            link for link in device_links if link.get("deviceType") == "emc" and not link.get("stopAt")
        ]
        if not active_links:
            return None
        active_links.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""))
        return active_links[-1]

    def _build_evse_update_payload(
        self,
        wallbox: HagerWallboxSnapshot,
        charging_mode: str,
    ) -> dict[str, Any]:
        parameters = wallbox.evse_parameters
        sun_mode = _build_sun_mode_payload(parameters, charging_mode)

        parameter_keys = [
            "protection",
            "priority",
            "activated",
            "useMidMeter",
            "pulseWeight",
            "sunMode",
            "authenticationMode",
            "phasesManagement",
            "minCurrent",
            "chargeInFallbackModeAllowed",
            "fallbackMaxCurrent",
            "minCurrentSunMode",
            "ledIntensity",
            "lockCable",
            "phaseMapping",
        ]

        payload_parameters = {key: parameters.get(key) for key in parameter_keys if key in parameters}
        payload_parameters["sunMode"] = sun_mode

        payload: dict[str, Any] = {
            "deviceName": wallbox.evse.get("deviceName") or wallbox.display_name,
            "parameters": payload_parameters,
        }

        if wallbox.media == "ModbusTCP":
            evse_sub_type_parameters = wallbox.evse.get("evseSubTypeParameters") or {}
            payload["evseSubTypeParameters"] = {
                key: evse_sub_type_parameters.get(key)
                for key in (
                    "ocppActivation",
                    "ocppServerAddress",
                    "ocppId",
                    "ocppAuthType",
                    "deviceReference",
                    "wbType",
                )
                if key in evse_sub_type_parameters
            }

        return payload

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        expect_json: bool = True,
        json_payload: dict[str, Any] | None = None,
        retry_on_auth_error: bool = True,
    ) -> Any:
        access_token = await self.async_get_access_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        }

        try:
            async with self._session.request(method, url, headers=headers, json=json_payload) as response:
                if response.status == 401:
                    if retry_on_auth_error:
                        await self.async_refresh_access_token()
                        return await self._request_json(
                            method,
                            url,
                            expect_json=expect_json,
                            json_payload=json_payload,
                            retry_on_auth_error=False,
                        )
                    raise HagerAuthenticationError("Hager rejected the access token")

                if response.status == 403:
                    raise HagerAuthenticationError("Hager refused access to this Flow resource")
                if response.status == 404:
                    raise HagerApiError(f"Hager API path not found: {url}")
                if response.status >= 400:
                    body = await response.text()
                    raise HagerApiError(_build_http_error_message(response.status, url, body))

                if not expect_json:
                    await response.read()
                    return None
                if response.status == 204:
                    return None

                text = await response.text()
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError as err:
                    raise HagerApiError(f"Unexpected non-JSON response from Hager at {url}") from err
        except HagerApiError:
            raise
        except ClientError as err:
            raise HagerApiConnectionError(f"Unable to reach Hager API URL {url}") from err

    def _persist_tokens(self, access_token: str, reauth_token: str) -> None:
        updated = {
            **self._entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REAUTH_TOKEN: reauth_token,
        }
        self._hass.config_entries.async_update_entry(self._entry, data=updated)
