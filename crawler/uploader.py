import os
import mimetypes
import logging
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from PIL import Image
import io

LOGGER = logging.getLogger(__name__)


class S3Uploader:
    def __init__(self):
        endpoint = os.getenv('S3_ENDPOINT', '').strip() or None
        self.client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv('S3_ACCESS_KEY') or None,
            aws_secret_access_key=os.getenv('S3_SECRET_KEY') or None,
            region_name=os.getenv('S3_REGION', 'ap-east-1')
        )
        self.bucket = os.getenv('S3_BUCKET', 'tg-crawler-media-ffe95227')
        self._init_public_url()
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code == '404':
                try:
                    self.client.create_bucket(Bucket=self.bucket)
                    LOGGER.info("Created bucket: %s", self.bucket)
                except ClientError as ce:
                    LOGGER.warning("Bucket '%s' not found and could not be created: %s", self.bucket, ce)
            else:
                LOGGER.warning("Bucket check failed (code=%s): %s", code, e)
        except Exception as e:
            LOGGER.warning("Unable to verify bucket '%s': %s. Proceeding optimistically.", self.bucket, e)

    def _init_public_url(self):
        explicit = os.getenv('S3_PUBLIC_ENDPOINT', '').strip()
        if explicit:
            self.public_url_base = explicit.rstrip('/')
            return
        endpoint = os.getenv('S3_ENDPOINT', '').strip()
        if not endpoint or 'amazonaws.com' in endpoint:
            region = os.getenv('S3_REGION', 'ap-east-1')
            self.public_url_base = f"https://{self.bucket}.s3.{region}.amazonaws.com"
        else:
            self.public_url_base = f"{endpoint.rstrip('/')}/{self.bucket}"

    def _public_url(self, key: str) -> str:
        return f"{self.public_url_base}/{key}"

    def upload_media(self, local_path: str, channel: str, message_id: int, seq: int = 0) -> dict:
        ext = Path(local_path).suffix or '.bin'
        date_folder = __import__('datetime').datetime.now().strftime('%Y/%m/%d')
        s3_key = f"{channel}/{date_folder}/{message_id}_{seq}{ext}"
        content_type = mimetypes.guess_type(local_path)[0] or 'application/octet-stream'

        self.client.upload_file(
            local_path, self.bucket, s3_key,
            ExtraArgs={'ContentType': content_type}
        )

        thumb_key = None
        if content_type.startswith('image/'):
            thumb_key = f"{channel}/{date_folder}/{message_id}_{seq}_thumb.jpg"
            try:
                with Image.open(local_path) as img:
                    img.thumbnail((400, 400))
                    thumb_buffer = io.BytesIO()
                    img.save(thumb_buffer, format='JPEG', quality=75)
                    thumb_buffer.seek(0)
                    self.client.put_object(
                        Bucket=self.bucket, Key=thumb_key,
                        Body=thumb_buffer, ContentType='image/jpeg'
                    )
            except Exception as e:
                LOGGER.warning("Thumbnail failed for %s: %s", local_path, e)
                thumb_key = None

        return {
            's3_key': s3_key,
            's3_url': self._public_url(s3_key),
            'thumb_key': thumb_key,
            'thumb_url': self._public_url(thumb_key) if thumb_key else None,
        }

    def upload_photo(self, local_path: str, channel: str, message_id: int, seq: int = 0) -> dict:
        return self.upload_media(local_path, channel, message_id, seq)
