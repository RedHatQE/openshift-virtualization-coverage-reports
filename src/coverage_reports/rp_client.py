"""ReportPortal API client.

Provides authenticated access to the ReportPortal REST API with
automatic pagination and thread-safe request handling.
All connection details come from config/env vars — nothing hardcoded.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
import urllib3

LOGGER = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RPClient:
    """ReportPortal REST API client with pagination support.

    Args:
        base_url: ReportPortal instance URL.
        project: RP project name.
        token: Bearer token for authentication.
    """

    def __init__(self, base_url: str, project: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.token = token
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        self.session.verify = False

    def _api_url(self, path: str) -> str:
        """Build a full API URL for the given resource path.

        Args:
            path: Resource path relative to the project API root.

        Returns:
            Fully qualified API URL.
        """
        return f"{self.base_url}/api/v1/{self.project}/{path}"

    def _paginate(
        self,
        url: str,
        params: dict[str, Any],
        page_size: int = 300,
    ) -> list[dict[str, Any]]:
        """Fetch all pages from a paginated RP endpoint.

        Thread-safe: acquires a lock per request to protect the
        shared session.

        Args:
            url: Full API URL to query.
            params: Query parameters to include in each request.
            page_size: Number of items per page.

        Returns:
            Accumulated list of content items from all pages.

        Raises:
            requests.HTTPError: If any page request returns a non-2xx status.
        """
        all_items: list[dict[str, Any]] = []
        page_num = 1
        total_pages = 1

        while page_num <= total_pages:
            paginated_params = {**params, "page.size": page_size, "page.page": page_num}
            with self._lock:
                response = self.session.get(url=url, params=paginated_params)
            response.raise_for_status()
            data = response.json()

            total_pages = data["page"]["totalPages"]
            LOGGER.info(f"Fetching page {page_num}/{total_pages} from {url}")

            all_items.extend(data.get("content", []))
            page_num += 1

        return all_items

    def get_launches(
        self,
        bundle_prefix: str,
        since_days: int = 0,
        page_size: int = 300,
    ) -> list[dict[str, Any]]:
        """Fetch launches filtered by BUNDLE attribute prefix.

        Args:
            bundle_prefix: Prefix to match against BUNDLE attribute values.
            since_days: Only fetch launches from the last N days (0 = all).
            page_size: Number of items per page.

        Returns:
            List of launch dicts matching the filter.
        """
        url = self._api_url(path="launch")
        params: dict[str, Any] = {
            "filter.has.attributeKey": "BUNDLE",
            "filter.cnt.attributeValue": bundle_prefix,
        }
        if since_days > 0:
            since_ts = int((datetime.now(tz=UTC) - timedelta(days=since_days)).timestamp() * 1000)
            params["filter.gte.startTime"] = since_ts
            LOGGER.info(f"Filtering launches to last {since_days} days")

        launches = self._paginate(url=url, params=params, page_size=page_size)
        LOGGER.info(f"Found {len(launches)} launches matching bundle prefix '{bundle_prefix}'")
        return launches

    def get_test_items(
        self,
        launch_id: int,
        page_size: int = 300,
    ) -> list[dict[str, Any]]:
        """Fetch all test items for a given launch.

        Args:
            launch_id: Launch ID to fetch items for.
            page_size: Number of items per page.

        Returns:
            List of test item dicts.
        """
        url = self._api_url(path="item")
        params: dict[str, Any] = {"filter.eq.launchId": launch_id}
        return self._paginate(url=url, params=params, page_size=page_size)
