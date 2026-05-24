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


def _as_number(value: Any) -> float | int | None:
    """Convert a Hager number-like value to a numeric type."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace(",", ".")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _sum_numeric_values(*values: Any) -> float | int | None:
    """Return the sum of all numeric values when at least one is present."""
    numbers = [number for value in values if (number := _as_number(value)) is not None]
    if not numbers:
        return None
    return sum(numbers)


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


def _normalize_charge_strategy_configuration(
    configuration: dict[str, Any] | None,
) -> list[dict[str, int]]:
    """Return a normalized 7-day Hager charge strategy configuration."""
    rows = (configuration or {}).get("chargeStrategyConfiguration")
    rows_by_weekday: dict[int, dict[str, Any]] = {}

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            weekday = _as_number(row.get("chargeStrategyWeekday"))
            if weekday is None:
                continue
            weekday_index = int(weekday)
            if 0 <= weekday_index <= 6:
                rows_by_weekday[weekday_index] = row

    normalized_rows: list[dict[str, int]] = []
    for weekday in range(7):
        row = rows_by_weekday.get(weekday, {})
        normalized_rows.append(
            {
                "chargeStrategyWeekday": weekday,
                "chargeStrategyUnit": int(_as_number(row.get("chargeStrategyUnit")) or 0),
                "chargeStrategyDirectChargeAmount": int(
                    _as_number(row.get("chargeStrategyDirectChargeAmount")) or 0
                ),
                "chargeStrategyDelayedChargeAmount": int(
                    _as_number(row.get("chargeStrategyDelayedChargeAmount")) or 0
                ),
                "chargeStrategyDelayedChargeTime": int(
                    _as_number(row.get("chargeStrategyDelayedChargeTime")) or 0
                ),
            }
        )

    return normalized_rows


def _normalize_parameter_list(configuration: dict[str, Any] | None) -> list[dict[str, int]]:
    """Return a normalized 7-day Hager parameter list."""
    rows = (configuration or {}).get("parameterList")
    normalized_rows: list[dict[str, int]] = []

    if isinstance(rows, list):
        for row in rows[:7]:
            if not isinstance(row, dict):
                normalized_rows.append({"daytime": 0, "minEnergy": 0})
                continue
            normalized_rows.append(
                {
                    "daytime": int(_as_number(row.get("daytime")) or 0),
                    "minEnergy": int(_as_number(row.get("minEnergy")) or 0),
                }
            )

    while len(normalized_rows) < 7:
        normalized_rows.append({"daytime": 0, "minEnergy": 0})

    return normalized_rows


def _normalize_sun_mode_parameter_list(parameters: dict[str, Any] | None) -> list[dict[str, int]]:
    """Return a normalized 7-day live sun mode parameter list."""
    sun_mode = (parameters or {}).get("sunMode")
    if not isinstance(sun_mode, dict):
        return []

    rows = sun_mode.get("parameterList")
    if not isinstance(rows, list):
        return []

    normalized_rows: list[dict[str, int]] = []
    for row in rows[:7]:
        if not isinstance(row, dict):
            normalized_rows.append({"daytime": 0, "minEnergy": 0})
            continue
        normalized_rows.append(
            {
                "daytime": int(_as_number(row.get("daytime")) or 0),
                "minEnergy": int(_as_number(row.get("minEnergy")) or 0),
            }
        )

    while len(normalized_rows) < 7:
        normalized_rows.append({"daytime": 0, "minEnergy": 0})

    return normalized_rows


def _charge_mode_from_configuration(configuration: dict[str, Any] | None) -> str | None:
    """Map the wallbox configuration payload to a stable charge mode label."""
    if not isinstance(configuration, dict) or not configuration:
        return None

    if configuration.get("chargeFull") is True:
        return CHARGING_MODE_BOOST

    active_charge_strategy = str(configuration.get("activeChargeStrategy") or "").strip().casefold()
    rows = _normalize_charge_strategy_configuration(configuration)
    params = _normalize_parameter_list(configuration)

    has_minimum_energy = any(
        row["chargeStrategyDirectChargeAmount"] > 0 or param["minEnergy"] > 0
        for row, param in zip(rows, params, strict=True)
    )
    has_delayed_target = any(
        row["chargeStrategyDelayedChargeTime"] > 0 or param["daytime"] > 0
        for row, param in zip(rows, params, strict=True)
    )

    if active_charge_strategy == "deactivated":
        return CHARGING_MODE_SOLAR_ONLY
    if active_charge_strategy == "delayed":
        if has_delayed_target:
            return CHARGING_MODE_SOLAR_DELAYED
        if has_minimum_energy:
            return CHARGING_MODE_SOLAR_MINIMUM
        return CHARGING_MODE_SOLAR_ONLY
    return None


def _first_positive_int(*values: Any) -> int:
    """Return the first strictly positive integer value."""
    for value in values:
        number = _as_number(value)
        if number is None:
            continue
        integer = int(number)
        if integer > 0:
            return integer
    return 0


def _charging_mode_from_parameters(parameters: dict[str, Any]) -> str | None:
    """Map the raw Hager sun mode payload to a stable charge mode label."""
    sun_mode = parameters.get("sunMode") or {}
    if not isinstance(sun_mode, dict):
        return None

    activated = sun_mode.get("activated")
    strategy = sun_mode.get("chargingStrategy")
    normalized_strategy = strategy.casefold() if isinstance(strategy, str) else None
    parameter_list = _normalize_sun_mode_parameter_list(parameters)
    has_minimum_energy = any(row["minEnergy"] > 0 for row in parameter_list)
    has_delayed_target = any(row["daytime"] > 0 for row in parameter_list)

    if activated is False:
        return CHARGING_MODE_BOOST
    if normalized_strategy == SUN_MODE_DISABLED.casefold():
        return CHARGING_MODE_SOLAR_ONLY
    if normalized_strategy == SUN_MODE_IMMEDIATE.casefold():
        return CHARGING_MODE_SOLAR_MINIMUM
    if normalized_strategy == SUN_MODE_DELAYED.casefold():
        if has_delayed_target:
            return CHARGING_MODE_SOLAR_DELAYED
        if has_minimum_energy:
            return CHARGING_MODE_SOLAR_MINIMUM
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
        sun_mode["chargingStrategy"] = SUN_MODE_DELAYED
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
    status: dict[str, Any] | None

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
        timestamp = (
            (self.status or {}).get("time")
            or self.emc_device_link.get("lastKnownDeviceStatusTimestamp")
            or self.installation.get("lastKnownDeviceStatusTimestamp")
        )
        return str(timestamp) if timestamp else None

    @property
    def properties(self) -> dict[str, Any]:
        return self.overview or {}

    @property
    def live_status(self) -> dict[str, Any]:
        return self.status or {}

    @property
    def grid_power(self) -> float | int | None:
        direct_power = _sum_numeric_values(
            self.live_status.get("POWER_ROOTLM_L1"),
            self.live_status.get("POWER_ROOTLM_L2"),
            self.live_status.get("POWER_ROOTLM_L3"),
        )
        if direct_power is not None:
            return direct_power

        powermeters = self.live_status.get("powermeters") or []
        if isinstance(powermeters, list):
            for powermeter in powermeters:
                if not isinstance(powermeter, dict):
                    continue
                if str(powermeter.get("deviceType") or "").casefold() != "root":
                    continue
                root_power = _sum_numeric_values(
                    powermeter.get("L1"),
                    powermeter.get("L2"),
                    powermeter.get("L3"),
                )
                if root_power is not None:
                    return root_power

        return _as_number((self.overview or {}).get("wallboxCurrentOverview", {}).get("NET"))

    @property
    def home_power(self) -> float | int | None:
        direct_power = _sum_numeric_values(
            self.live_status.get("POWER_C_L1"),
            self.live_status.get("POWER_C_L2"),
            self.live_status.get("POWER_C_L3"),
        )
        if direct_power is not None:
            return direct_power
        return None

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
    status: dict[str, Any] | None
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
        timestamp = (
            (self.status or {}).get("time")
            or self.meter.get("lastKnownDeviceStatusTimestamp")
            or self.meter.get("updatedAt")
        )
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
        if self.device_type != "PVExtern":
            return None

        powermeters = (self.status or {}).get("powermeters") or []
        if isinstance(powermeters, list):
            meter_status = next(
                (
                    powermeter
                    for powermeter in powermeters
                    if isinstance(powermeter, dict)
                    and (
                        str(powermeter.get("deviceId") or "") == self.meter_id
                        or str(powermeter.get("id") or "") == self.meter_id
                    )
                ),
                None,
            )
            if isinstance(meter_status, dict):
                phase_power = _sum_numeric_values(
                    meter_status.get("L1"),
                    meter_status.get("L2"),
                    meter_status.get("L3"),
                )
                if phase_power is not None:
                    return abs(phase_power)

        # The Flow sub-device endpoints expose the PV meter identity but not a
        # dedicated live power field. Using the wallbox SUN overview here was
        # misleading because it represents the wallbox solar contribution, not
        # the production meter value.
        direct_candidates = (
            self.meter.get("currentPower"),
            self.meter.get("power"),
            self.meter.get("activePower"),
            self.type_parameters.get("currentPower"),
            self.type_parameters.get("power"),
            self.type_parameters.get("activePower"),
        )
        for candidate in direct_candidates:
            if candidate is not None:
                return candidate

        return None

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
    def sun_mode(self) -> dict[str, Any]:
        sun_mode = self.evse_parameters.get("sunMode") or {}
        return sun_mode if isinstance(sun_mode, dict) else {}

    @property
    def charging_mode(self) -> str | None:
        live_mode = _charging_mode_from_parameters(self.evse_parameters)
        if live_mode is not None:
            return live_mode
        return _charge_mode_from_configuration(self.properties)

    @property
    def charge_strategy_configuration(self) -> list[dict[str, int]]:
        return _normalize_charge_strategy_configuration(self.properties)

    @property
    def parameter_list(self) -> list[dict[str, int]]:
        live_parameter_list = _normalize_sun_mode_parameter_list(self.evse_parameters)
        if live_parameter_list:
            return live_parameter_list
        return _normalize_parameter_list(self.properties)

    @property
    def minimum_energy(self) -> int:
        """Return the first configured minimum-energy target."""
        minimum_energy = 0
        for configuration_row, parameter_row in zip(
            self.charge_strategy_configuration,
            self.parameter_list,
            strict=True,
        ):
            minimum_energy = max(
                minimum_energy,
                _first_positive_int(
                    configuration_row.get("chargeStrategyDirectChargeAmount"),
                    parameter_row.get("minEnergy"),
                ),
            )
        return minimum_energy

    @property
    def delayed_target_time(self) -> int:
        """Return the first configured delayed target time."""
        delayed_target_time = 0
        for configuration_row, parameter_row in zip(
            self.charge_strategy_configuration,
            self.parameter_list,
            strict=True,
        ):
            delayed_target_time = max(
                delayed_target_time,
                _first_positive_int(
                    configuration_row.get("chargeStrategyDelayedChargeTime"),
                    parameter_row.get("daytime"),
                ),
            )
        return delayed_target_time

    @property
    def authentication_mode(self) -> str | None:
        value = self.evse_parameters.get("authenticationMode")
        return str(value) if value else None

    @property
    def phases_management(self) -> str | None:
        value = self.evse_parameters.get("phasesManagement")
        return str(value) if value else None

    @property
    def lock_cable(self) -> bool | None:
        value = self.evse_parameters.get("lockCable")
        if value is None:
            value = self.properties.get("cableLock")
        if value is None:
            return None
        return bool(value)

    @property
    def charge_in_fallback_mode_allowed(self) -> bool | None:
        value = self.evse_parameters.get("chargeInFallbackModeAllowed")
        if value is None:
            value = self.properties.get("chargeInFallbackModeAllowed")
        if value is None:
            return None
        return bool(value)

    @property
    def led_intensity(self) -> int | None:
        value = _as_number(self.evse_parameters.get("ledIntensity"))
        if value is None:
            value = _as_number(self.properties.get("ledIntensity"))
        if value is None:
            return None
        return int(value)

    @property
    def solar_holding_time(self) -> int | None:
        value = _as_number(self.sun_mode.get("holdingTimeInMin"))
        if value is None:
            value = _as_number(self.properties.get("chargeStopHysteresis"))
        if value is None:
            return None
        return int(value)

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
        self._wallbox_configuration_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._wallbox_charge_strategy_memory: dict[str, dict[str, list[dict[str, int]]]] = {}

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

    def prime_cached_snapshot(self, snapshot: HagerAccountSnapshot | None) -> None:
        """Prime the live configuration cache from a stored snapshot."""
        if snapshot is None:
            return

        for wallbox in snapshot.wallboxes.values():
            if wallbox.configuration is None:
                continue
            self._wallbox_configuration_cache[
                (wallbox.emc_hardware_id, wallbox.configuration_id)
            ] = dict(wallbox.configuration)
            self._remember_wallbox_charge_strategy(wallbox)
            self._hydrate_wallbox_sun_mode_profile(wallbox)

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
        storage_status_results = await asyncio.gather(
            *[
                self._get_storage_status(str(emc.get("deviceId")))
                for installation, emc in sub_device_requests
            ],
            return_exceptions=True,
        )

        emcs: list[HagerEmcSnapshot] = []
        meters: list[HagerMeterSnapshot] = []
        wallboxes: list[HagerWallboxSnapshot] = []

        for index, ((installation, emc), sub_devices) in enumerate(
            zip(sub_device_requests, sub_devices_results, strict=True)
        ):
            storage_status_result = storage_status_results[index]
            if isinstance(storage_status_result, Exception):
                LOGGER.debug(
                    "Unable to load live storage status for %s: %s",
                    emc.get("deviceId"),
                    storage_status_result,
                )
                storage_status = None
            else:
                storage_status = storage_status_result

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

            raw_configurations = await asyncio.gather(
                *[
                    self._get_wallbox_configuration(
                        str(emc.get("deviceId")),
                        _configuration_id_from_evse(evse),
                    )
                    for evse in evses
                ]
            )

            configurations: list[dict[str, Any] | None] = []
            for evse, configuration in zip(evses, raw_configurations, strict=True):
                cache_key = (str(emc.get("deviceId")), _configuration_id_from_evse(evse))
                if isinstance(configuration, dict):
                    self._wallbox_configuration_cache[cache_key] = dict(configuration)
                    configurations.append(configuration)
                    continue

                cached_configuration = self._wallbox_configuration_cache.get(cache_key)
                if cached_configuration is not None:
                    LOGGER.debug(
                        "Reusing cached Hager wallbox configuration for %s/%s",
                        cache_key[0],
                        cache_key[1],
                    )
                    configurations.append(dict(cached_configuration))
                    continue

                configurations.append(None)

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
                    status=storage_status,
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
                        status=storage_status,
                        meter_group_size=len(meter_devices),
                    )
                )

            for evse, configuration in zip(evses, configurations, strict=True):
                wallbox = HagerWallboxSnapshot(
                    installation=installation,
                    emc_device_link=emc,
                    evse=evse,
                    configuration=configuration if isinstance(configuration, dict) else None,
                )
                self._remember_wallbox_charge_strategy(wallbox)
                self._hydrate_wallbox_sun_mode_profile(wallbox)
                wallboxes.append(wallbox)

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
        normalized_mode = _normalize_charging_mode(charging_mode)
        self._remember_wallbox_charge_strategy(wallbox)

        if normalized_mode == CHARGING_MODE_BOOST:
            await self._async_update_wallbox_settings(
                wallbox,
                sun_mode_updates={
                    "activated": False,
                    "chargingStrategy": SUN_MODE_DISABLED,
                    "parameterList": [],
                },
            )
            return

        if normalized_mode == CHARGING_MODE_SOLAR_ONLY:
            await self._async_update_wallbox_settings(
                wallbox,
                sun_mode_updates={
                    "activated": True,
                    "chargingStrategy": SUN_MODE_DISABLED,
                    "parameterList": [],
                },
            )
            return

        parameter_rows = wallbox.parameter_list
        remembered = self._wallbox_charge_strategy_memory.get(wallbox.device_id) or {}
        remembered_params = remembered.get("parameterList", [])
        updated_parameter_rows: list[dict[str, str]] = []
        has_minimum_energy = False
        has_delayed_target = False

        for weekday in range(7):
            parameter_row = parameter_rows[weekday]
            remembered_param = remembered_params[weekday] if weekday < len(remembered_params) else {}
            minimum_energy = _first_positive_int(
                parameter_row.get("minEnergy"),
                remembered_param.get("minEnergy"),
            )
            delayed_target = _first_positive_int(
                parameter_row.get("daytime"),
                remembered_param.get("daytime"),
            )

            if minimum_energy > 0:
                has_minimum_energy = True
            if delayed_target > 0:
                has_delayed_target = True

            updated_parameter_rows.append(
                {
                    "daytime": str(0 if normalized_mode == CHARGING_MODE_SOLAR_MINIMUM else delayed_target),
                    "minEnergy": str(minimum_energy),
                }
            )

        if not has_minimum_energy:
            raise HagerApiError(
                "Configure a non-zero minimum energy in Hager first, then retry from Home Assistant"
            )

        if normalized_mode == CHARGING_MODE_SOLAR_DELAYED and not has_delayed_target:
            raise HagerApiError(
                "Configure a delayed target time in Hager first, then retry from Home Assistant"
            )

        await self._async_update_wallbox_settings(
            wallbox,
            sun_mode_updates={
                "activated": True,
                "chargingStrategy": SUN_MODE_DELAYED,
                "parameterList": updated_parameter_rows,
            },
        )

    async def async_set_lock_cable(self, wallbox: HagerWallboxSnapshot, enabled: bool) -> None:
        await self._async_update_wallbox_settings(
            wallbox,
            parameter_updates={"lockCable": enabled},
        )

    async def async_set_charge_in_fallback_mode(
        self,
        wallbox: HagerWallboxSnapshot,
        allowed: bool,
    ) -> None:
        await self._async_update_wallbox_settings(
            wallbox,
            parameter_updates={"chargeInFallbackModeAllowed": allowed},
        )

    async def async_set_led_intensity(self, wallbox: HagerWallboxSnapshot, intensity: float) -> None:
        await self._async_update_wallbox_settings(
            wallbox,
            parameter_updates={"ledIntensity": max(0, min(100, int(round(intensity))))},
        )

    async def async_set_solar_holding_time(
        self,
        wallbox: HagerWallboxSnapshot,
        minutes: float,
    ) -> None:
        await self._async_update_wallbox_settings(
            wallbox,
            sun_mode_updates={"holdingTimeInMin": max(0, int(round(minutes)))},
        )

    async def async_set_boost_mode(self, wallbox: HagerWallboxSnapshot, enabled: bool) -> None:
        await self._async_update_wallbox_configuration(wallbox, {"chargeFull": enabled})

    async def async_set_charge_strategy(self, wallbox: HagerWallboxSnapshot, charging_strategy: str) -> None:
        await self.async_set_charging_mode(wallbox, charging_strategy)

    def _remember_wallbox_charge_strategy(self, wallbox: HagerWallboxSnapshot) -> None:
        """Keep the last delayed target profile so switching back stays possible."""
        rows = wallbox.charge_strategy_configuration
        params = wallbox.parameter_list
        existing = self._wallbox_charge_strategy_memory.get(wallbox.device_id) or {}
        existing_rows = existing.get("chargeStrategyConfiguration", [])
        existing_params = existing.get("parameterList", [])

        merged_rows: list[dict[str, int]] = []
        merged_params: list[dict[str, int]] = []
        has_meaningful_profile = False

        for weekday, (row, param) in enumerate(zip(rows, params, strict=True)):
            existing_row = existing_rows[weekday] if weekday < len(existing_rows) else {}
            existing_param = existing_params[weekday] if weekday < len(existing_params) else {}

            direct_amount = _first_positive_int(
                param.get("minEnergy"),
                row.get("chargeStrategyDirectChargeAmount"),
                existing_param.get("minEnergy"),
                existing_row.get("chargeStrategyDirectChargeAmount"),
            )
            delayed_time = _first_positive_int(
                param.get("daytime"),
                row.get("chargeStrategyDelayedChargeTime"),
                existing_param.get("daytime"),
                existing_row.get("chargeStrategyDelayedChargeTime"),
            )

            merged_rows.append(
                {
                    "chargeStrategyWeekday": weekday,
                    "chargeStrategyUnit": int(
                        row.get("chargeStrategyUnit")
                        or existing_row.get("chargeStrategyUnit")
                        or 0
                    ),
                    "chargeStrategyDirectChargeAmount": direct_amount,
                    "chargeStrategyDelayedChargeAmount": 0,
                    "chargeStrategyDelayedChargeTime": delayed_time,
                }
            )
            merged_params.append(
                {
                    "daytime": delayed_time,
                    "minEnergy": direct_amount,
                }
            )

            if direct_amount > 0 or delayed_time > 0:
                has_meaningful_profile = True

        if not has_meaningful_profile:
            return

        self._wallbox_charge_strategy_memory[wallbox.device_id] = {
            "chargeStrategyConfiguration": merged_rows,
            "parameterList": merged_params,
        }

    def _hydrate_wallbox_sun_mode_profile(self, wallbox: HagerWallboxSnapshot) -> None:
        """Reinject the remembered delayed profile when Hager omits it in live sun mode."""
        remembered = self._wallbox_charge_strategy_memory.get(wallbox.device_id) or {}
        remembered_params = remembered.get("parameterList", [])
        if not remembered_params:
            return

        evse_parameters = wallbox.evse.setdefault("parameters", {})
        if not isinstance(evse_parameters, dict):
            return

        sun_mode = evse_parameters.setdefault("sunMode", {})
        if not isinstance(sun_mode, dict):
            return

        current_parameter_list = sun_mode.get("parameterList")
        if isinstance(current_parameter_list, list) and current_parameter_list:
            return

        sun_mode["parameterList"] = [
            {
                "daytime": str(int(row.get("daytime") or 0)),
                "minEnergy": str(int(row.get("minEnergy") or 0)),
            }
            for row in remembered_params
            if isinstance(row, dict)
        ]

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
                        json={"reAuthToken": reauth_token},
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

    async def _get_storage_status(self, emc_hardware_id: str) -> dict[str, Any] | None:
        """Return the live storage status when the endpoint is available."""
        access_token = await self.async_get_access_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        }
        url = f"{E3DC_AUTH_BASE_URL}/storages/{emc_hardware_id}/status"

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (400, 403, 404):
                    await response.read()
                    return None

                if response.status == 401:
                    access_token = await self.async_refresh_access_token()
                    headers["Authorization"] = f"Bearer {access_token}"
                    async with self._session.get(url, headers=headers) as retry_response:
                        if retry_response.status in (400, 403, 404):
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
                f"Unable to reach the Hager storage status API for {emc_hardware_id}"
            ) from err

        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise HagerApiError("Unexpected response shape from the Hager storage status API")
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
        charging_mode: str | None = None,
        *,
        parameter_updates: dict[str, Any] | None = None,
        sun_mode_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = wallbox.evse_parameters
        if charging_mode is not None:
            sun_mode = _build_sun_mode_payload(parameters, charging_mode)
        else:
            sun_mode = dict(parameters.get("sunMode") or {})
            if sun_mode_updates:
                sun_mode.update(sun_mode_updates)
        if isinstance(sun_mode, dict) and sun_mode.get("parameterList") is None:
            # Hager rejects null here for generic EVSE updates, but accepts [].
            sun_mode["parameterList"] = []
        if (
            isinstance(sun_mode, dict)
            and str(sun_mode.get("chargingStrategy") or "").casefold() == SUN_MODE_DISABLED.casefold()
            and isinstance(sun_mode.get("parameterList"), list)
            and sun_mode["parameterList"]
            and charging_mode is None
        ):
            # The remembered solar profile is useful for the mode selector, but Hager
            # rejects generic EVSE updates when Solar only is sent with a non-empty
            # parameterList. Keep the memory in HA, but send an empty list to Hager.
            sun_mode["parameterList"] = []

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
        if parameter_updates:
            payload_parameters.update(parameter_updates)
        if sun_mode or "sunMode" in parameters:
            payload_parameters["sunMode"] = sun_mode

        payload: dict[str, Any] = {
            "deviceName": wallbox.evse.get("deviceName") or wallbox.display_name,
            "parameters": payload_parameters,
        }

        if wallbox.media == "ModbusTCP":
            evse_sub_type_parameters = wallbox.evse.get("evseSubTypeParameters") or {}
            payload["evseSubTypeParameters"] = {
                "deviceReference": evse_sub_type_parameters.get("deviceReference") or "",
                "ocppActivation": bool(evse_sub_type_parameters.get("ocppActivation", False)),
                "ocppAuthType": evse_sub_type_parameters.get("ocppAuthType") or "No",
                "ocppId": evse_sub_type_parameters.get("ocppId")
                or evse_sub_type_parameters.get("wallboxId")
                or wallbox.wallbox_id,
                "ocppServerAddress": evse_sub_type_parameters.get("ocppServerAddress") or "wss://",
                "wbType": str(
                    evse_sub_type_parameters.get("wbType")
                    or wallbox.properties.get("wallboxType")
                    or ""
                ),
            }

        return payload

    async def _async_update_wallbox_settings(
        self,
        wallbox: HagerWallboxSnapshot,
        charging_mode: str | None = None,
        *,
        parameter_updates: dict[str, Any] | None = None,
        sun_mode_updates: dict[str, Any] | None = None,
    ) -> None:
        payload = self._build_evse_update_payload(
            wallbox,
            charging_mode,
            parameter_updates=parameter_updates,
            sun_mode_updates=sun_mode_updates,
        )
        await self._request_json(
            "put",
            (
                f"{INSTALLATIONS_BASE_URL}/installations/{wallbox.installation_id}"
                f"/device-links/{wallbox.emc_link_id}/sub/controlled/{wallbox.evse_id}"
            ),
            json_payload=payload,
            expect_json=False,
        )

    async def _async_update_wallbox_configuration(
        self,
        wallbox: HagerWallboxSnapshot,
        updates: dict[str, Any],
    ) -> None:
        """Update the live wallbox configuration using the supported E3/DC endpoint."""
        await self._request_json(
            "put",
            f"{E3DC_AUTH_BASE_URL}/wallboxes/{wallbox.emc_hardware_id}/{wallbox.configuration_id}/configuration",
            json_payload=updates,
            expect_json=False,
        )

        cache_key = (wallbox.emc_hardware_id, wallbox.configuration_id)
        fresh_configuration = await self._get_wallbox_configuration(
            wallbox.emc_hardware_id,
            wallbox.configuration_id,
        )
        if isinstance(fresh_configuration, dict):
            self._wallbox_configuration_cache[cache_key] = dict(fresh_configuration)
            wallbox.configuration = dict(fresh_configuration)
            self._remember_wallbox_charge_strategy(wallbox)
            return

        cached_configuration = dict(
            self._wallbox_configuration_cache.get(cache_key) or wallbox.configuration or {}
        )
        cached_configuration.update(updates)
        self._wallbox_configuration_cache[cache_key] = cached_configuration
        wallbox.configuration = dict(cached_configuration)
        self._remember_wallbox_charge_strategy(wallbox)

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
