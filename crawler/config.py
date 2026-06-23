import os
from pathlib import Path

from dotenv import load_dotenv


_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / '.env')
load_dotenv(_HERE.parent / '.env')


class Config:
    API_ID: int = int(os.getenv('TG_API_ID', 0))
    API_HASH: str = os.getenv('TG_API_HASH', '')
    PHONE: str = os.getenv('TG_PHONE', '')
    SESSION_NAME: str = 'session/tg_session'

    DATABASE_URL: str = os.getenv('DATABASE_URL', 'postgresql://tguser:tgpwd@localhost:5432/tg_crawler')

    S3_ENDPOINT: str = os.getenv('S3_ENDPOINT', 'http://localhost:9000')
    S3_PUBLIC_ENDPOINT: str = os.getenv('S3_PUBLIC_ENDPOINT', os.getenv('S3_ENDPOINT', 'http://localhost:9000'))
    S3_ACCESS_KEY: str = os.getenv('S3_ACCESS_KEY', 'minioadmin')
    S3_SECRET_KEY: str = os.getenv('S3_SECRET_KEY', 'minioadmin')
    S3_BUCKET: str = os.getenv('S3_BUCKET', 'tg-crawler-media-ffe95227')
    S3_REGION: str = os.getenv('S3_REGION', 'ap-east-1')

    TG_PROXY_TYPE: str = os.getenv('TG_PROXY_TYPE', '').strip().lower()
    TG_PROXY_HOST: str = os.getenv('TG_PROXY_HOST', '').strip()
    TG_PROXY_PORT: int = int(os.getenv('TG_PROXY_PORT', '0') or 0)
    TG_PROXY_USERNAME: str = os.getenv('TG_PROXY_USERNAME', '').strip()
    TG_PROXY_PASSWORD: str = os.getenv('TG_PROXY_PASSWORD', '').strip()

    _channels = os.getenv('TARGET_CHANNELS', 'haijiaoxuanfei')
    TARGET_CHANNELS = [c.strip() for c in _channels.split(',') if c.strip()]

    CRAWLER_OWNER_USER_ID: int = int(os.getenv('CRAWLER_OWNER_USER_ID', '0') or 0)
