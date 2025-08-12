from typing import Any, Optional
from urllib.parse import urljoin

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class StremThruError(Exception):
    def __init__(self, error: dict[str, Any]):
        self.type = error.get("type", "")
        self.code = error.get("code", "")
        self.message = error.get("message", "")
        self.store_name = error.get("store_name", "")


class StremThru(DebridClient):
    AGENT = "mediafusion"
    auth: str | dict

    def __init__(self, url: str, token: str, **kwargs):
        self.BASE_URL = url
        if ":" in token:
            parts = token.split(":")
            self.auth = {
                "store": parts[0],
                "token": parts[1],
            }
        else:
            self.auth = token
        super().__init__(token)

    async def initialize_headers(self):
        self.headers["User-Agent"] = self.AGENT
        if isinstance(self.auth, str):
            self.headers["Proxy-Authorization"] = f"Basic {self.auth}"
        elif isinstance(self.auth, dict):
            self.headers["X-StremThru-Store-Name"] = self.auth["store"]
            self.headers["X-StremThru-Store-Authorization"] = (
                f"Bearer {self.auth['token']}"
            )

    def __del__(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        if error_data.get("error"):
            error = error_data.get(
                "error", {"message": "unknown error", "code": "UNKNOWN"}
            )
            error_code = error.get("code")
            match error_code:
                case "FORBIDDEN" | "UNAUTHORIZED":
                    raise ProviderException(
                        "Invalid Token / Permission Denied", "invalid_token.mp4"
                    )
                case "PAYMENT_REQUIRED":
                    raise ProviderException("Need to upgrade plan", "need_premium.mp4")
                case "TOO_MANY_REQUESTS":
                    raise ProviderException(
                        "Too many requests", "too_many_requests.mp4"
                    )
                case "UNAVAILABLE_FOR_LEGAL_REASONS":
                    raise ProviderException(
                        "Content marked as infringing", "content_infringing.mp4"
                    )
                case "STORE_LIMIT_EXCEEDED":
                    raise ProviderException(
                        "Hit max limit", "exceed_remote_traffic_limit.mp4"
                    )
                case _:
                    raise ProviderException(
                        f"StremThru Error: {str(error)}",
                        "api_error.mp4",
                    )
        raise ProviderException(
            f"StremThru Error: {str(error_data)}",
            "api_error.mp4",
        )

    async def disable_access_token(self):
        pass

    async def _make_request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        is_http_response: bool = False,
        is_expected_to_fail: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        params = params or {}
        full_url = urljoin(self.BASE_URL, url)
        response = await super()._make_request(
            method=method,
            url=full_url,
            params=params,
            is_http_response=is_http_response,
            is_expected_to_fail=is_expected_to_fail,
            **kwargs,
        )
        if is_http_response or is_expected_to_fail:
            return response
        return response.get("data")

    async def add_magnet_link(self, magnet_link):
        response_data = await self._make_request(
            "POST", "/v0/store/magnets", json={"magnet": magnet_link}
        )
        return response_data

    async def get_user_torrent_list(self):
        return await self._make_request("GET", "/v0/store/magnets")

    async def get_torrent_info(self, torrent_id):
        response = await self._make_request("GET", "/v0/store/magnets/" + torrent_id)
        return response

    async def get_torrent_instant_availability(
        self,
        magnet_links: list[str],
        stremio_video_id: str,
        is_http_response: bool = False,
    ):
        params = {"magnet": ",".join(magnet_links), "sid": stremio_video_id}
        return await self._make_request(
            "GET",
            "/v0/store/magnets/check",
            params=params,
            is_http_response=is_http_response,
        )

    async def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = await self.get_user_torrent_list()
        for torrent in available_torrents["items"]:
            if torrent["hash"] == info_hash:
                return torrent

    async def create_download_link(self, link):
        response = await self._make_request(
            "POST",
            "/v0/store/link/generate",
            json={"link": link},
            is_expected_to_fail=True,
        )
        if response.get("data"):
            return response["data"]
        error_message = response.get("error", "unknown error")
        raise ProviderException(
            f"Failed to create download link from StremThru {str(error_message)}",
            "transfer_error.mp4",
        )

    async def delete_torrent(self, magnet_id):
        return await self._make_request(
            "DELETE",
            "/v0/store/magnets/" + magnet_id,
        )

    async def get_user_info(self, is_http_response: bool = False):
        return await self._make_request(
            "GET", "/v0/store/user", is_http_response=is_http_response
        )
