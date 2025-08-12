from datetime import timedelta
from typing import Dict, Any, List, Optional

import PTT
import httpx

from db.config import settings
from db.models import MediaFusionMetaData, TorrentStreams
from db.schemas import UserData
from scrapers.stremio_addons import StremioScraper
from utils.parser import (
    convert_size_to_bytes,
)
from utils.runtime_const import TORRENTIO_SEARCH_TTL

SUPPORTED_DEBRID_SERVICE = {
    "realdebrid",
    "premiumize",
    "alldebrid",
    "debridlink",
    "offcloud",
    "torbox",
}


class TorrentioScraper(StremioScraper):
    cache_key_prefix = "torrentio"

    def __init__(self):
        super().__init__(
            cache_key_prefix=self.cache_key_prefix,
            base_url=settings.torrentio_url,
            logger_name=__name__,
        )
        self.http_client = httpx.AsyncClient(
            timeout=30, proxy=settings.requests_proxy_url
        )

    def _generate_url(
        self,
        user_data: UserData,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> str:
        user_data_str = (
            f"/{user_data.streaming_provider.service}={user_data.streaming_provider.token.rstrip('=')}"
            if user_data.streaming_provider.service in SUPPORTED_DEBRID_SERVICE
            else ""
        )
        url = f"{self.base_url}{user_data_str}/stream/{catalog_type}/{metadata.id}.json"
        if catalog_type == "series":
            url = f"{self.base_url}{user_data_str}/stream/{catalog_type}/{metadata.id}:{season}:{episode}.json"
        return url

    @StremioScraper.cache(ttl=TORRENTIO_SEARCH_TTL)
    @StremioScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        return await super()._scrape_and_parse(
            user_data, metadata, catalog_type, season, episode
        )

    def get_adult_content_field(self, stream_data: Dict[str, Any]) -> str:
        return stream_data["title"]

    def get_scraper_name(self) -> str:
        return "Torrentio"

    def parse_stream_title(self, stream: dict) -> tuple[dict, bool]:
        try:
            descriptions = stream.get("title")
            torrent_name = descriptions.splitlines()[0]
            metadata = PTT.parse_title(torrent_name, True)
            source = stream["name"].splitlines()[0].split()[-1]
            info_hash = stream.get("infoHash")
            if not info_hash:
                url = stream["url"]
                info_hash = url.split("/")[5]

            is_cached = "+" in stream["name"]
            metadata.update(
                {
                    "source": source,
                    "info_hash": info_hash,
                    "size": convert_size_to_bytes(
                        self.extract_size_string(descriptions)
                    ),
                    "torrent_name": torrent_name,
                    "seeders": self.extract_seeders(descriptions),
                    "filename": stream.get("behaviorHints", {}).get("filename"),
                }
            )
            return metadata, is_cached
        except Exception as e:
            self.metrics.record_error("title_parsing_error")
            raise e
