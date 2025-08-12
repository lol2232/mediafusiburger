import scrapy
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from db.config import settings
from db.crud import get_stream_by_info_hash
from utils import torrent


class TorrentDownloadAndParsePipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        torrent_link = adapter.get("torrent_link")

        if not torrent_link:
            raise DropItem(f"No torrent link found in item: {item}")

        headers = {"Referer": item.get("webpage_url")}

        response = await maybe_deferred_to_future(
            spider.crawler.engine.download(
                scrapy.Request(torrent_link, callback=NO_CALLBACK, headers=headers),
            )
        )

        if response.status != 200:
            spider.logger.error(
                f"Failed to download torrent file: {response.url} with status {response.status}"
            )
            return item

        # Validate the content-type of the response
        if "application/x-bittorrent" not in response.headers.get(
            "Content-Type", b""
        ).decode("utf-8", "ignore"):
            spider.logger.error(
                f"Unexpected Content-Type for {response.url}: {response.headers.get('Content-Type')}"
            )
            return item

        torrent_metadata = torrent.extract_torrent_metadata(
            response.body, item.get("parsed_data")
        )

        if not torrent_metadata:
            return item

        item.update(torrent_metadata)
        return item


class MagnetDownloadAndParsePipeline:
    async def process_item(self, item, spider):
        magnet_link = item.get("magnet_link")

        if not magnet_link:
            raise DropItem(f"No magnet link found in item: {item}")

        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise DropItem(f"Failed to parse info_hash from magnet link: {magnet_link}")
        if torrent_stream := await get_stream_by_info_hash(info_hash):
            if (
                item.get("expected_sources")
                and torrent_stream.source not in item["expected_sources"]
            ):
                spider.logger.info(
                    "Source mismatch for %s: %s != %s. Trying to re-create the data",
                    torrent_stream.torrent_name,
                    item["source"],
                    torrent_stream.source,
                )
                await torrent_stream.delete()
            else:
                raise DropItem(
                    f"Torrent stream already exists: {torrent_stream.torrent_name} from {torrent_stream.source}"
                )

        torrent_metadata = await torrent.info_hashes_to_torrent_metadata(
            [info_hash], trackers
        )

        if not torrent_metadata:
            if item.get("file_data"):
                return item
            raise DropItem(f"Failed to extract torrent metadata: {item}")

        item.update(torrent_metadata[0])
        return item
