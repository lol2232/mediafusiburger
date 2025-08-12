import asyncio
import logging
from uuid import uuid4

from scrapy import signals
from scrapy.exceptions import DropItem

from db import crud
from db.crud import organize_episodes
from db.models import (
    TorrentStreams,
    MediaFusionSeriesMetaData,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import TVMetaData


class QueueBasedPipeline:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing_task = None

    async def init(self):
        self.processing_task = asyncio.create_task(self.process_queue())

    async def close(self):
        await self.queue.join()
        self.processing_task.cancel()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.init, signal=signals.spider_opened)
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item, spider):
        # Instead of processing the item directly, add it to the queue
        await self.queue.put((item, spider))
        return item

    async def process_queue(self):
        logging.info("Starting processing queue")
        while True:
            item, spider = await self.queue.get()
            try:
                await self.parse_item(item, spider)
            except Exception as e:
                logging.error(f"Error processing item: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def parse_item(self, item, spider):
        raise NotImplementedError


class EventSeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        series = await MediaFusionSeriesMetaData.find_one({"title": item["title"]})

        if not series:
            meta_id = f"mf{uuid4().fields[-1]}"

            # Create an initial entry for the series
            series = MediaFusionSeriesMetaData(
                id=meta_id,
                title=item["title"],
                year=item["year"],
                poster=item.get("poster"),
                background=item.get("background"),
                is_poster_working=bool(item.get("poster")),
                is_add_title_to_poster=item.get("is_add_title_to_poster", False),
            )
            await series.insert()
            logging.info("Added series %s", series.title)

        torrent_stream = await TorrentStreams.find_one({"_id": item["info_hash"]})
        if not torrent_stream:
            torrent_stream = TorrentStreams(
                id=item["info_hash"],
                meta_id=series.id,
                torrent_name=item["torrent_name"],
                announce_list=item["announce_list"],
                size=item["total_size"],
                languages=item["languages"],
                resolution=item.get("resolution"),
                codec=item.get("codec"),
                quality=item.get("quality"),
                audio=item.get("audio"),
                hdr=item.get("hdr"),
                source=item["source"],
                uploader=item.get("uploader"),
                catalog=item["catalog"],
                created_at=item["created_at"],
                episode_files=item.get("episodes"),
                seeders=item["seeders"],
            )
            await torrent_stream.insert()
            logging.info(
                "Added torrent stream %s for series %s",
                torrent_stream.torrent_name,
                series.title,
            )

        await organize_episodes(series.id)
        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item


class TVStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        await crud.save_tv_channel_metadata(tv_metadata)
        return item


class MovieStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "movie":
            return item

        await crud.save_movie_metadata(item, item.get("is_search_imdb_title", True))
        return item


class SeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "series":
            return item
        await crud.save_series_metadata(item)
        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item


class LiveEventStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"name not found in item: {item}")

        await crud.save_events_data(item)
        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item
