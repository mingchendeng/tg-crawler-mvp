import json
import os
import mimetypes
import logging
import tempfile
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

        self.local_client = None
        self.local_bucket = None
        self.local_public_url_base = None
        local_endpoint = os.getenv('S3_LOCAL_ENDPOINT', '').strip()
        if local_endpoint:
            self.local_client = boto3.client(
                's3',
                endpoint_url=local_endpoint,
                aws_access_key_id=os.getenv('S3_LOCAL_ACCESS_KEY', 'minioadmin'),
                aws_secret_access_key=os.getenv('S3_LOCAL_SECRET_KEY', 'minioadmin'),
                region_name='us-east-1',
            )
            self.local_bucket = os.getenv('S3_LOCAL_BUCKET', '') or self.bucket
            local_public = os.getenv('S3_LOCAL_PUBLIC_ENDPOINT', '').strip()
            if local_public:
                self.local_public_url_base = f"{local_public.rstrip('/')}/{self.local_bucket}"
            self._ensure_local_bucket()

    def _ensure_bucket(self):
        created = False
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code == '404':
                try:
                    self.client.create_bucket(Bucket=self.bucket)
                    LOGGER.info("Created bucket: %s", self.bucket)
                    created = True
                except ClientError as ce:
                    LOGGER.warning("Bucket '%s' not found and could not be created: %s", self.bucket, ce)
            else:
                LOGGER.warning("Bucket check failed (code=%s): %s", code, e)
        except Exception as e:
            LOGGER.warning("Unable to verify bucket '%s': %s. Proceeding optimistically.", self.bucket, e)

        self._set_public_read_policy(created)

    def _set_public_read_policy(self, created: bool):
        try:
            public_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self.bucket}/*"]
                }]
            }
            self.client.put_bucket_policy(Bucket=self.bucket, Policy=json.dumps(public_policy))
            if created:
                LOGGER.info("Set public-read policy on new bucket: %s", self.bucket)
            else:
                LOGGER.info("Updated public-read policy on bucket: %s", self.bucket)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code == 'AccessDenied':
                LOGGER.warning(
                    "Cannot set public-read policy on bucket '%s': AccessDenied. "
                    "If using AWS S3, check 'Block Public Access' settings. "
                    "If using MinIO, ensure credentials have admin privileges.",
                    self.bucket,
                )
            else:
                LOGGER.warning("Could not set bucket public-read policy (code=%s): %s", code, e)
        except Exception as e:
            LOGGER.warning("Could not set bucket public-read policy: %s. Images may not be accessible.", e)

    def _ensure_local_bucket(self):
        if not self.local_client:
            return
        try:
            self.local_client.head_bucket(Bucket=self.local_bucket)
        except ClientError:
            try:
                self.local_client.create_bucket(Bucket=self.local_bucket)
                LOGGER.info("Created local MinIO bucket: %s", self.local_bucket)
            except Exception as e:
                LOGGER.warning("Cannot create local bucket '%s': %s", self.local_bucket, e)
                return
        except Exception as e:
            LOGGER.warning("Cannot check local bucket '%s': %s", self.local_bucket, e)
            return
        self._set_local_public_read_policy()

    def _set_local_public_read_policy(self):
        if not self.local_client:
            return
        try:
            policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self.local_bucket}/*"]
                }]
            }
            self.local_client.put_bucket_policy(Bucket=self.local_bucket, Policy=json.dumps(policy))
        except Exception as e:
            LOGGER.warning("Could not set local bucket public-read policy: %s", e)

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

    @staticmethod
    def _date_folder(msg_date):
        if msg_date:
            return msg_date.strftime('%Y/%m')
        return __import__('datetime').datetime.now().strftime('%Y/%m')

    def _make_key(self, channel, date_folder, message_id, seq, ext):
        return f"{channel}/{date_folder}/{message_id}_{seq}{ext}"

    def _upload_to_client(self, client, bucket, local_path, s3_key, content_type, public_url_base):
        client.upload_file(local_path, bucket, s3_key, ExtraArgs={'ContentType': content_type})
        return f"{public_url_base}/{s3_key}"

    def _make_thumb(self, local_path, thumb_key, client, bucket, public_url_base):
        try:
            with Image.open(local_path) as img:
                img.thumbnail((400, 400))
                thumb_buffer = io.BytesIO()
                img.save(thumb_buffer, format='JPEG', quality=75)
                thumb_buffer.seek(0)
                client.put_object(Bucket=bucket, Key=thumb_key, Body=thumb_buffer, ContentType='image/jpeg')
            return f"{public_url_base}/{thumb_key}"
        except Exception as e:
            LOGGER.warning("Thumbnail failed for %s: %s", local_path, e)
            return None

    def upload_media(self, local_path: str, channel: str, message_id: int, seq: int = 0, msg_date=None) -> dict:
        ext = Path(local_path).suffix or '.bin'
        date_folder = self._date_folder(msg_date)
        content_type = mimetypes.guess_type(local_path)[0] or 'application/octet-stream'

        s3_key = self._make_key(channel, date_folder, message_id, seq, ext)
        thumb_key = f"{channel}/{date_folder}/{message_id}_{seq}_thumb.jpg" if content_type.startswith('image/') else None

        s3_url = self._upload_to_client(self.client, self.bucket, local_path, s3_key, content_type, self.public_url_base)
        thumb_url = self._make_thumb(local_path, thumb_key, self.client, self.bucket, self.public_url_base) if thumb_key else None

        local_s3_url = None
        local_thumb_url = None
        if self.local_client and self.local_public_url_base:
            try:
                local_s3_url = self._upload_to_client(self.local_client, self.local_bucket, local_path, s3_key, content_type, self.local_public_url_base)
                if thumb_key:
                    local_thumb_url = self._make_thumb(local_path, thumb_key, self.local_client, self.local_bucket, self.local_public_url_base)
            except Exception as e:
                LOGGER.warning("Local MinIO upload failed for %s: %s", local_path, e)

        return {
            's3_key': s3_key,
            's3_url': s3_url,
            'thumb_key': thumb_key,
            'thumb_url': thumb_url,
            'local_s3_url': local_s3_url,
            'local_thumb_url': local_thumb_url,
        }

    def upload_photo(self, local_path: str, channel: str, message_id: int, seq: int = 0, msg_date=None) -> dict:
        return self.upload_media(local_path, channel, message_id, seq, msg_date)

    def retry_local_mirror(self, s3_key: str, thumb_key: str = None):
        if not self.local_client or not self.local_public_url_base:
            return None, None
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            self.client.download_file(self.bucket, s3_key, tmp_path)
            content_type = mimetypes.guess_type(tmp_path)[0] or 'application/octet-stream'
            local_s3_url = self._upload_to_client(self.local_client, self.local_bucket, tmp_path, s3_key, content_type, self.local_public_url_base)
            local_thumb_url = None
            if thumb_key and content_type.startswith('image/'):
                local_thumb_url = self._make_thumb(tmp_path, thumb_key, self.local_client, self.local_bucket, self.local_public_url_base)
            return local_s3_url, local_thumb_url
        except Exception as e:
            LOGGER.warning("retry_local_mirror failed for s3_key=%s: %s", s3_key, e)
            return None, None
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
