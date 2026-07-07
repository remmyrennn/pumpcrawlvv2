"""
HTTP utility helpers.

Thin wrappers around the shared ``httpx.AsyncClient`` that add consistent
error handling, logging, and response validation. No module should create
its own HTTP client — they must import the shared client from
``app.http_client``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.utils.errors import HttpClientError

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────────

JsonPayload = dict[str, Any] | list[Any]


# ── Response helpers ──────────────────────────────────────────────────────────

def assert_ok(response: httpx.Response) -> httpx.Response:
    """
    Raise :class:`~app.utils.errors.HttpClientError` when the response
    status code indicates an error.

    Parameters
    ----------
    response:
        The httpx response object to inspect.

    Returns
    -------
    httpx.Response
        The same response, unchanged, on success.

    Raises
    ------
    HttpClientError
        When ``response.status_code >= 400``.
    """
    if response.is_error:
        raise HttpClientError(
            f"HTTP {response.status_code} for {response.url}: {response.text[:200]}",
            code="HTTP_ERROR",
        )
    return response


def parse_json(response: httpx.Response) -> JsonPayload:
    """
    Parse the response body as JSON, raising a descriptive error on failure.

    Parameters
    ----------
    response:
        The httpx response to parse.

    Returns
    -------
    dict | list
        Parsed JSON payload.

    Raises
    ------
    HttpClientError
        When the body cannot be decoded as JSON.
    """
    try:
        return response.json()
    except Exception as exc:
        raise HttpClientError(
            f"Failed to parse JSON from {response.url}: {exc}",
            code="JSON_PARSE_ERROR",
        ) from exc


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JsonPayload:
    """
    Perform a GET request, validate the response, and return parsed JSON.

    Parameters
    ----------
    client:
        The shared async HTTP client.
    url:
        Target URL.
    params:
        Optional query parameters.
    headers:
        Optional additional request headers.

    Returns
    -------
    dict | list
        Parsed JSON response body.

    Raises
    ------
    HttpClientError
        On any HTTP or JSON parse error.
    """
    logger.debug("GET %s params=%s", url, params)
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise HttpClientError(
            f"GET request failed for {url}: {exc}",
            code="REQUEST_FAILED",
        ) from exc
    return parse_json(assert_ok(response))


async def safe_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: JsonPayload | None = None,
    headers: dict[str, str] | None = None,
) -> JsonPayload:
    """
    Perform a POST request, validate the response, and return parsed JSON.

    Parameters
    ----------
    client:
        The shared async HTTP client.
    url:
        Target URL.
    json:
        Optional JSON body.
    headers:
        Optional additional request headers.

    Returns
    -------
    dict | list
        Parsed JSON response body.

    Raises
    ------
    HttpClientError
        On any HTTP or JSON parse error.
    """
    logger.debug("POST %s", url)
    try:
        response = await client.post(url, json=json, headers=headers)
    except httpx.HTTPError as exc:
        raise HttpClientError(
            f"POST request failed for {url}: {exc}",
            code="REQUEST_FAILED",
        ) from exc
    return parse_json(assert_ok(response))
