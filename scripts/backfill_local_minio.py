"""Backfill local_s3_url for media_files that lack local MinIO mirror."""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler.uploader import S3Uploader
from crawler.db import Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
LOGGER = logging.getLogger('backfill_local_minio')


def main():
    uploader = S3Uploader()
    if not uploader.local_client:
        LOGGER.error("Local MinIO client not configured (S3_LOCAL_ENDPOINT is empty). Aborting.")
        sys.exit(1)

    db = Database()
    total = 0
    while True:
        rows = db.fetchall(
            """SELECT id, s3_key, thumb_key
               FROM media_files
               WHERE local_s3_url IS NULL
                 AND s3_key IS NOT NULL
               LIMIT 200"""
        )
        if not rows:
            break

        count = 0
        for r in rows:
            local_s3_url, local_thumb_url = uploader.retry_local_mirror(r['s3_key'], r['thumb_key'])
            if local_s3_url:
                db.execute(
                    "UPDATE media_files SET local_s3_url = %s, local_thumb_url = %s WHERE id = %s",
                    (local_s3_url, local_thumb_url, r['id']),
                )
                db.commit()
                count += 1
            else:
                LOGGER.warning("Failed to mirror media id=%d s3_key=%s, will retry later", r['id'], r['s3_key'])

        total += count
        LOGGER.info("Backfilled %d media files this batch (total: %d)", count, total)

    LOGGER.info("Done. Total media files mirrored to local MinIO: %d", total)


if __name__ == '__main__':
    main()
