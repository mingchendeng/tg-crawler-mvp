import os
import sys
import socket
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from psycopg2.extras import Json
from config import Config
from db import Database, has_meaningful_extracted
from extractor import LooseExtractor
from uploader import S3Uploader
from dedupe_llm import LLMDeduper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('crawler')

CRAWLER_LOCK_KEY = 2026051201

class IncrementalCrawler:
    def __init__(self):
        os.makedirs('session', exist_ok=True)
        self.db = Database()
        self.owner_user_id = Config.CRAWLER_OWNER_USER_ID or None
        self.runtime_settings = self._resolve_runtime_settings()
        self.api_id = int(self.runtime_settings.get('api_id') or 0)
        self.api_hash = self.runtime_settings.get('api_hash') or ''
        self.phone = self.runtime_settings.get('phone') or ''
        self.target_channels = self.runtime_settings.get('target_channels') or []
        self.proxy_type = self.runtime_settings.get('proxy_type') or ''
        self.proxy_host = self.runtime_settings.get('proxy_host') or ''
        self.proxy_port = int(self.runtime_settings.get('proxy_port') or 0)
        self.proxy_username = self.runtime_settings.get('proxy_username') or ''
        self.proxy_password = self.runtime_settings.get('proxy_password') or ''

        self.client = TelegramClient(
            Config.SESSION_NAME,
            self.api_id,
            self.api_hash,
            proxy=self._build_proxy(),
        )
        self.extractor = LooseExtractor()
        self.uploader = S3Uploader()
        self.deduper = LLMDeduper()
        self.tmp_dir = Path(tempfile.gettempdir()) / 'tg_media'
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_runtime_settings(self) -> Dict[str, Any]:
        settings = {
            'api_id': Config.API_ID,
            'api_hash': Config.API_HASH,
            'phone': Config.PHONE,
            'target_channels': list(Config.TARGET_CHANNELS),
            'proxy_type': Config.TG_PROXY_TYPE,
            'proxy_host': Config.TG_PROXY_HOST,
            'proxy_port': Config.TG_PROXY_PORT,
            'proxy_username': Config.TG_PROXY_USERNAME,
            'proxy_password': Config.TG_PROXY_PASSWORD,
        }
        if not self.owner_user_id:
            return settings

        row = self.db.fetch_user_crawler_settings(self.owner_user_id)
        if not row:
            logger.warning('CRAWLER_OWNER_USER_ID=%s has no user_crawler_settings row, fallback to env values', self.owner_user_id)
            return settings

        if row.get('tg_api_id'):
            settings['api_id'] = int(row['tg_api_id'])
        if row.get('tg_api_hash'):
            settings['api_hash'] = str(row['tg_api_hash']).strip()
        if row.get('tg_phone'):
            settings['phone'] = str(row['tg_phone']).strip()

        channels = [str(x).strip() for x in (row.get('target_channels') or []) if str(x).strip()]
        if channels:
            settings['target_channels'] = channels

        settings['proxy_type'] = str(row.get('tg_proxy_type') or settings['proxy_type'] or '').strip().lower()
        settings['proxy_host'] = str(row.get('tg_proxy_host') or settings['proxy_host'] or '').strip()
        settings['proxy_port'] = int(row.get('tg_proxy_port') or settings['proxy_port'] or 0)
        settings['proxy_username'] = str(row.get('tg_proxy_username') or settings['proxy_username'] or '').strip()
        settings['proxy_password'] = str(row.get('tg_proxy_password') or settings['proxy_password'] or '').strip()
        return settings

    def _build_proxy(self):
        if not self.proxy_host or not self.proxy_port:
            return None

        proxy_host = self.proxy_host
        if proxy_host == 'host.docker.internal' and not Path('/.dockerenv').exists():
            proxy_host = '127.0.0.1'

        # Test if proxy is reachable before using it
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.5)
            result = sock.connect_ex((proxy_host, self.proxy_port))
            sock.close()
            if result != 0:
                logger.warning('Proxy %s:%s is unreachable, falling back to direct connection', proxy_host, self.proxy_port)
                return None
        except Exception as e:
            logger.warning('Proxy check failed (%s), falling back to direct connection', e)
            return None

        try:
            import socks
        except Exception:
            logger.error('Proxy configured but PySocks is missing. Please install pysocks.')
            return None

        proxy_type_map = {
            'socks5': socks.SOCKS5,
            'socks4': socks.SOCKS4,
            'http': socks.HTTP,
        }
        proxy_type = proxy_type_map.get(self.proxy_type, socks.SOCKS5)

        logger.info('Using Telegram proxy: %s://%s:%s', self.proxy_type or 'socks5', proxy_host, self.proxy_port)
        return (
            proxy_type,
            proxy_host,
            self.proxy_port,
            True,
            self.proxy_username or None,
            self.proxy_password or None,
        )

    async def run(self):
        if not self.api_id or not self.api_hash:
            logger.error("Missing TG_API_ID or TG_API_HASH")
            return
        if not self.phone:
            logger.error('Missing TG_PHONE')
            return

        lock_acquired = self.db.try_acquire_lock(CRAWLER_LOCK_KEY)
        if not lock_acquired:
            logger.warning('Another crawler instance is already running (advisory lock held). Exit current process.')
            return

        try:
            recovered = self.db.cleanup_stale_running_logs(30)
            if recovered:
                logger.warning('Recovered %s stale crawl_logs records from previous abnormal exits', recovered)

            await self.client.start(phone=self.phone)
            me = await self.client.get_me()
            logger.info(f"Logged in as: {me.username or me.id}")
            if self.owner_user_id:
                logger.info('Crawler owner mode enabled: owner_user_id=%s channels=%s', self.owner_user_id, len(self.target_channels))
            if self.deduper.is_configured():
                logger.info(
                    'LLM dedupe enabled (model=%s, candidates=%s)',
                    self.deduper.model,
                    self.deduper.candidate_limit,
                )
            else:
                logger.info('LLM dedupe disabled; only exact extracted code match when code is present')

            reports = []
            for channel_name in self.target_channels:
                reports.append(await self.crawl_channel(channel_name))

            done_count = sum(1 for r in reports if r['status'] == 'completed')
            failed_count = sum(1 for r in reports if r['status'] != 'completed')
            processed_total = sum(r['processed'] for r in reports)
            new_total = sum(r['new_count'] for r in reports)
            error_total = sum(r['errors_count'] for r in reports)
            logger.info(
                'Run finished. channels=%s completed=%s failed=%s processed=%s new=%s errors=%s',
                len(reports),
                done_count,
                failed_count,
                processed_total,
                new_total,
                error_total,
            )
        finally:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            try:
                self.db.release_lock(CRAWLER_LOCK_KEY)
            except Exception as e:
                logger.warning('Failed to release crawler lock: %s', e)
            self.db.close()

    @staticmethod
    def _append_error(error_details: List[str], text: str, limit: int = 50):
        if len(error_details) >= limit:
            return
        error_details.append(text[:500])

    @staticmethod
    def _parse_extracted_value(extracted_value: Any) -> Dict[str, Any]:
        """Normalizes extracted_json value to dict."""
        if isinstance(extracted_value, dict):
            return extracted_value
        if isinstance(extracted_value, str):
            try:
                loaded = json.loads(extracted_value)
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                return {}
        return {}

    @staticmethod
    def _merge_extracted(base: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
        """Fills missing person fields from fallback extracted payload."""
        merged = dict(base or {})
        for key, value in (fallback or {}).items():
            if key.startswith('_'):
                continue
            if key not in merged or merged.get(key) in (None, '', [], {}):
                merged[key] = value

        if '_found_fields' in fallback:
            merged['_found_fields'] = max(int(base.get('_found_fields') or 0), int(fallback.get('_found_fields') or 0))
        if 'confidence' in fallback:
            merged['confidence'] = max(float(base.get('confidence') or 0), float(fallback.get('confidence') or 0))
        if '_status' in fallback and (base.get('_status') in (None, '', 'failed')):
            merged['_status'] = fallback.get('_status')
        return merged

    @staticmethod
    def _extracted_score(extracted: Dict[str, Any]) -> int:
        score = int(extracted.get('_found_fields') or 0)
        if extracted.get('code'):
            score += 100
        if extracted.get('nickname'):
            score += 20
        return score

    def _apply_media_group_extracted(self, batch: List[dict], media_group_cache: Dict[int, Dict[str, Any]]):
        """Propagates best extracted result to media-only siblings in same album."""
        grouped_candidates: Dict[int, Dict[str, Any]] = {}

        for group_id, cached in (media_group_cache or {}).items():
            grouped_candidates[group_id] = {'score': int(cached.get('score') or 0), 'extracted': dict(cached.get('extracted') or {})}

        for item in batch:
            group_id = item.get('media_group_id')
            if not group_id:
                continue
            extracted = item.get('extracted_obj') or {}
            if not has_meaningful_extracted(extracted):
                continue

            current = grouped_candidates.get(group_id)
            score = self._extracted_score(extracted)
            if current is None or score > current['score']:
                grouped_candidates[group_id] = {'score': score, 'extracted': dict(extracted)}

        if not grouped_candidates:
            return

        for group_id, candidate in grouped_candidates.items():
            existing = media_group_cache.get(group_id)
            if existing is None or candidate['score'] > int(existing.get('score') or 0):
                media_group_cache[group_id] = {'score': candidate['score'], 'extracted': dict(candidate['extracted'])}

        for item in batch:
            group_id = item.get('media_group_id')
            if not group_id:
                continue
            candidate = grouped_candidates.get(group_id)
            if not candidate:
                continue

            current = item.get('extracted_obj') or {}
            if has_meaningful_extracted(current):
                continue

            merged = self._merge_extracted(current, candidate['extracted'])
            item['extracted_obj'] = merged
            item['extracted_json'] = json.dumps(merged)
            item['extract_confidence'] = merged.get('confidence', 0)
            item['status'] = merged.get('_status', 'pending')

    def _backfill_missing_profiles(self, channel_id: int, limit: int = 500):
        rows = self.db.fetchall(
            """
            SELECT m.id, m.extracted_json
            FROM messages m
            LEFT JOIN profiles p ON p.message_id = m.id
            WHERE m.channel_id = %s
              AND p.id IS NULL
              AND (%s::bigint IS NULL OR m.owner_user_id = %s)
            ORDER BY m.id DESC
            LIMIT %s
            """,
            (channel_id, self.owner_user_id, self.owner_user_id, limit),
        )
        if not rows:
            return 0

        count = 0
        for row in rows:
            extracted = self._parse_extracted_value(row[1] if len(row) > 1 else None)
            if self.db.upsert_profile_from_extracted(row[0], extracted, owner_user_id=self.owner_user_id):
                self._link_profile_to_person(channel_id, row[0], extracted)
                count += 1
        return count

    @staticmethod
    def _extracted_from_profile_fields(
        internal_code: Any,
        display_nickname: Any,
        province: Any,
        city: Any,
        age: Any,
        height: Any,
        weight: Any,
        cup_size: Any,
        occupation: Any,
        introduction_fee: Any,
        monthly_allowance: Any,
    ) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        if internal_code:
            extracted['code'] = str(internal_code)
        if display_nickname:
            extracted['nickname'] = str(display_nickname)
        if province:
            extracted['province'] = str(province)
        if city:
            extracted['city'] = str(city)
        if age:
            extracted['age'] = int(age)
        if height:
            extracted['height'] = int(height)
        if weight:
            extracted['weight'] = int(weight)
        if cup_size:
            extracted['cup'] = str(cup_size)
        if occupation:
            extracted['occupation'] = str(occupation)
        if introduction_fee is not None:
            extracted['intro_fee'] = float(introduction_fee)
        if monthly_allowance is not None:
            extracted['monthly_allowance'] = float(monthly_allowance)

        if extracted:
            extracted['_found_fields'] = len([k for k in extracted.keys() if not k.startswith('_')])
            extracted['confidence'] = max(0.45, min(0.8, round(extracted['_found_fields'] / 20, 2)))
            extracted['_status'] = 'review'
        return extracted

    def _backfill_media_group_profiles(
        self,
        channel_id: int,
        limit_groups: int = 300,
        group_ids: List[int] = None,
    ):
        """Repairs old album rows by copying extracted fields within media_group_id."""
        if group_ids:
            normalized = sorted(set(int(g) for g in group_ids if g))
            groups = [(g,) for g in normalized]
        else:
            groups = self.db.fetchall(
                """
                SELECT media_group_id
                FROM messages
                WHERE channel_id = %s
                  AND media_group_id IS NOT NULL
                  AND (%s::bigint IS NULL OR owner_user_id = %s)
                GROUP BY media_group_id
                ORDER BY MAX(id) DESC
                LIMIT %s
                """,
                (channel_id, self.owner_user_id, self.owner_user_id, limit_groups),
            )
        if not groups:
            return 0

        repaired = 0
        for group_row in groups:
            media_group_id = group_row[0]
            rows = self.db.fetchall(
                """
                SELECT
                    m.id,
                    m.extracted_json,
                    p.internal_code,
                    p.display_nickname,
                    p.province,
                    p.city,
                    p.age,
                    p.height,
                    p.weight,
                    p.cup_size,
                    p.occupation,
                    p.introduction_fee,
                    p.monthly_allowance
                FROM messages m
                LEFT JOIN profiles p ON p.message_id = m.id
                WHERE channel_id = %s
                  AND media_group_id = %s
                  AND (%s::bigint IS NULL OR m.owner_user_id = %s)
                ORDER BY m.id ASC
                """,
                (channel_id, media_group_id, self.owner_user_id, self.owner_user_id),
            )
            if not rows:
                continue

            best_extracted = {}
            best_score = -1
            parsed_rows = []
            for row in rows:
                msg_id = row[0]
                extracted_json = row[1]
                extracted = self._parse_extracted_value(extracted_json)

                if not has_meaningful_extracted(extracted):
                    profile_extracted = self._extracted_from_profile_fields(
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                    )
                    if has_meaningful_extracted(profile_extracted):
                        extracted = self._merge_extracted(extracted, profile_extracted)

                parsed_rows.append((msg_id, extracted))
                if not has_meaningful_extracted(extracted):
                    continue
                score = self._extracted_score(extracted)
                if score > best_score:
                    best_score = score
                    best_extracted = extracted

            if not has_meaningful_extracted(best_extracted):
                continue

            for msg_id, extracted in parsed_rows:
                merged = self._merge_extracted(extracted, best_extracted)
                if merged != extracted:
                    self.db.execute(
                        """
                        UPDATE messages
                        SET extracted_json = %s,
                            extract_confidence = %s,
                            status = %s
                        WHERE id = %s
                        """,
                        (
                            Json(merged),
                            merged.get('confidence', 0),
                            merged.get('_status', 'pending'),
                            msg_id,
                        ),
                    )
                    self.db.commit()
                if self.db.upsert_profile_from_extracted(msg_id, merged, owner_user_id=self.owner_user_id):
                    self._link_profile_to_person(channel_id, msg_id, merged)
                    repaired += 1
        return repaired

    def _link_profile_to_person(self, channel_id: int, message_id: int, extracted: dict):
        row = self.db.fetchone("SELECT id FROM profiles WHERE message_id = %s", (message_id,))
        if not row:
            return
        code = extracted.get('code')
        person_id = self.db.ensure_person(channel_id, code, extracted, owner_user_id=self.owner_user_id)
        self.db.link_profile_to_person(row[0], person_id)

    def _recover_missing_local_mirror(self, channel_id: int, limit: int = 200):
        if not self.uploader.local_client:
            return 0
        rows = self.db.fetchall(
            """SELECT mf.id, mf.s3_key, mf.thumb_key
               FROM media_files mf
               JOIN messages m ON m.id = mf.message_id
               WHERE m.channel_id = %s
                 AND mf.local_s3_url IS NULL
                 AND mf.s3_key IS NOT NULL
               LIMIT %s""",
            (channel_id, limit),
        )
        if not rows:
            return 0

        count = 0
        for r in rows:
            local_s3_url, local_thumb_url = self.uploader.retry_local_mirror(r['s3_key'], r['thumb_key'])
            if local_s3_url:
                self.db.execute(
                    "UPDATE media_files SET local_s3_url = %s, local_thumb_url = %s WHERE id = %s",
                    (local_s3_url, local_thumb_url, r['id']),
                )
                self.db.commit()
                count += 1
        return count

    async def _flush_batch(
        self,
        channel_id: int,
        channel_name: str,
        batch: List[dict],
        media_messages: dict,
        media_group_cache: Dict[int, Dict[str, Any]],
    ):
        if not batch:
            return

        batch_group_ids = [int(item['media_group_id']) for item in batch if item.get('media_group_id')]

        self._apply_media_group_extracted(batch, media_group_cache)

        self.db.insert_batch_messages(batch)
        for item in batch:
            row = self.db.fetchone(
                """
                SELECT id FROM messages
                WHERE channel_id = %s
                  AND telegram_message_id = %s
                  AND (%s::bigint IS NULL OR owner_user_id = %s)
                """,
                (channel_id, item['telegram_message_id'], self.owner_user_id, self.owner_user_id)
            )
            if not row:
                continue

            message_db_id = row[0]
            extracted = item.get('extracted_obj') or {}
            if self.db.upsert_profile_from_extracted(message_db_id, extracted, owner_user_id=self.owner_user_id):
                self._link_profile_to_person(channel_id, message_db_id, extracted)

            if item['has_media']:
                source_message = media_messages.get(item['telegram_message_id'])
                if source_message is not None:
                    await self._handle_media(source_message, message_db_id, channel_name)

        if batch_group_ids:
            self._backfill_media_group_profiles(channel_id, group_ids=batch_group_ids)

        batch.clear()
        media_messages.clear()

    async def crawl_channel(self, channel_name: str):
        logger.info(f"Crawling: {channel_name}")
        processed = 0
        new_count = 0
        errors_count = 0
        error_details: List[str] = []
        status = 'failed'
        max_id = 0
        log_id = self.db.start_crawl_log(None, owner_user_id=self.owner_user_id)

        try:
            entity = await self.client.get_entity(channel_name)
            channel_id = self.db.upsert_channel(
                entity.id, entity.username or channel_name,
                getattr(entity, 'title', None), getattr(entity, 'about', None),
                owner_user_id=self.owner_user_id,
            )
            self.db.bind_crawl_log_channel(log_id, channel_id)
        except Exception as e:
            logger.error(f"Cannot get entity {channel_name}: {e}")
            errors_count += 1
            self._append_error(error_details, f'Cannot get entity {channel_name}: {e}')
            self.db.finish_crawl_log(log_id, 'failed', processed, new_count, errors_count, error_details)
            return {
                'channel': channel_name,
                'status': 'failed',
                'processed': processed,
                'new_count': new_count,
                'errors_count': errors_count,
            }

        try:
            last_id = self.db.get_last_msg_id(channel_name)
            logger.info(f"Resume from msg_id: {last_id}")

            repaired = self._backfill_missing_profiles(channel_id, 500)
            if repaired:
                logger.info('Backfilled %s missing profiles for channel=%s', repaired, channel_name)

            grouped_repaired = self._backfill_media_group_profiles(channel_id, 300)
            if grouped_repaired:
                logger.info('Backfilled %s media-group linked profiles for channel=%s', grouped_repaired, channel_name)

            batch = []
            media_messages = {}
            media_group_cache: Dict[int, Dict[str, Any]] = {}
            max_id = last_id

            async for message in self.client.iter_messages(entity, reverse=True, min_id=last_id):
                max_id = max(max_id, message.id)
                processed += 1

                try:
                    if not message.text and not message.media:
                        continue

                    existing = self.db.fetchone(
                        """
                        SELECT id FROM messages
                        WHERE channel_id = %s
                          AND telegram_message_id = %s
                          AND (%s::bigint IS NULL OR owner_user_id = %s)
                        """,
                        (channel_id, message.id, self.owner_user_id, self.owner_user_id)
                    )
                    if existing:
                        continue

                    text_content = (
                        message.text
                        or getattr(message, 'message', None)
                        or getattr(message, 'raw_text', None)
                        or ''
                    )

                    extracted = self.extractor.extract(text_content)

                    dup_db_id = await self._same_person_duplicate_id(channel_id, text_content, extracted)
                    if dup_db_id is not None:
                        logger.info(
                            'Skip telegram_msg_id=%s: same person as messages.id=%s (dedupe)',
                            message.id,
                            dup_db_id,
                        )
                        continue

                    new_count += 1

                    msg_data = {
                        'owner_user_id': self.owner_user_id,
                        'channel_id': channel_id,
                        'telegram_message_id': message.id,
                        'telegram_date': message.date,
                        'text_content': text_content,
                        'raw_json': json.dumps(message.to_dict(), default=str),
                        'has_media': message.media is not None,
                        'media_group_id': getattr(message, 'grouped_id', None),
                        'extracted_json': json.dumps(extracted),
                        'extracted_obj': extracted,
                        'extract_confidence': extracted.get('confidence', 0),
                        'status': extracted.get('_status', 'pending')
                    }

                    batch.append(msg_data)
                    if message.media:
                        media_messages[message.id] = message

                    if processed % 100 == 0:
                        await self._flush_batch(channel_id, channel_name, batch, media_messages, media_group_cache)
                        self.db.update_checkpoint(channel_name, max_id)
                        logger.info(f"Processed: {processed}, New: {new_count}, MaxID: {max_id}")
                except Exception as msg_error:
                    errors_count += 1
                    self._append_error(error_details, f'msg_id={getattr(message, "id", "unknown")}: {msg_error}')
                    logger.exception('Message process error on channel=%s msg_id=%s', channel_name, getattr(message, 'id', None))

            await self._flush_batch(channel_id, channel_name, batch, media_messages, media_group_cache)
            grouped_repaired_after = self._backfill_media_group_profiles(channel_id, 400)
            if grouped_repaired_after:
                logger.info('Post-run media-group repair updated %s rows for channel=%s', grouped_repaired_after, channel_name)
            self.db.update_checkpoint(channel_name, max_id)
            status = 'completed' if errors_count == 0 else 'partial'
        except Exception as e:
            errors_count += 1
            self._append_error(error_details, f'Channel level failure: {e}')
            logger.exception('Channel crawl failed: %s', channel_name)
            status = 'failed'

        self.db.finish_crawl_log(log_id, status, processed, new_count, errors_count, error_details)
        logger.info(
            'Channel %s done. status=%s processed=%s new=%s errors=%s',
            channel_name,
            status,
            processed,
            new_count,
            errors_count,
        )
        return {
            'channel': channel_name,
            'status': status,
            'processed': processed,
            'new_count': new_count,
            'errors_count': errors_count,
        }

    def _cleanup_media_paths(self, paths):
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    async def _handle_media(self, message, message_db_id: int, channel_name: str):
        downloaded_paths = []
        try:
            base_path = self.tmp_dir / f"{message.id}"
            paths = await message.download_media(file=str(base_path))
            if not paths:
                return
            if not isinstance(paths, list):
                paths = [paths]
            downloaded_paths = paths

            for idx, local_path in enumerate(paths):
                if not local_path or not os.path.exists(local_path):
                    continue

                media_type = self._detect_media_type(message, str(local_path))
                meta = self.uploader.upload_media(str(local_path), channel_name, message.id, idx, msg_date=message.date)

                ocr_text = None
                if media_type == 'photo':
                    try:
                        import pytesseract
                        ocr_text = pytesseract.image_to_string(str(local_path), lang='chi_sim')
                    except Exception:
                        pass

                self.db.insert_media(
                    message_db_id,
                    getattr(message, 'file_id', None),
                    media_type,
                    meta['s3_key'],
                    meta['s3_url'],
                    meta.get('thumb_key'),
                    meta.get('thumb_url'),
                    ocr_text,
                    os.path.getsize(local_path),
                    local_s3_url=meta.get('local_s3_url'),
                    local_thumb_url=meta.get('local_thumb_url'),
                    owner_user_id=self.owner_user_id,
                )
        except Exception as e:
            logger.error(f"Media error msg={message.id}: {e}")
        finally:
            self._cleanup_media_paths(downloaded_paths)

    def _detect_media_type(self, message, local_path: str) -> str:
        if isinstance(message.media, MessageMediaPhoto):
            return 'photo'

        if isinstance(message.media, MessageMediaDocument):
            mime = (getattr(getattr(message, 'file', None), 'mime_type', '') or '').lower()
            if mime.startswith('image/'):
                return 'photo'
            if mime.startswith('video/'):
                return 'video'
            if mime.startswith('audio/'):
                return 'audio'

        ext = Path(local_path).suffix.lower()
        if ext in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}:
            return 'photo'
        if ext in {'.mp4', '.mov', '.mkv', '.webm', '.avi'}:
            return 'video'
        if ext in {'.mp3', '.aac', '.wav', '.ogg', '.m4a'}:
            return 'audio'
        return 'document'

    async def _same_person_duplicate_id(self, channel_id: int, text_content: str, extracted: dict):
        code = extracted.get('code')
        if code is not None:
            hit = self.deduper.find_duplicate_by_code(self.db, channel_id, code, owner_user_id=self.owner_user_id)
            if hit is not None:
                return hit

        if not self.deduper.is_configured():
            return None

        candidates = self.db.fetch_dedupe_candidates(channel_id, self.deduper.candidate_limit, owner_user_id=self.owner_user_id)
        if not candidates:
            return None

        rows = [dict(r) for r in candidates]
        return await self.deduper.find_duplicate_db_id(text_content, extracted, rows)

async def main():
    crawler = IncrementalCrawler()
    await crawler.run()

if __name__ == '__main__':
    asyncio.run(main())
