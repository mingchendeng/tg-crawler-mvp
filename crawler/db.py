import psycopg2
import re
from typing import Any
from psycopg2.extras import RealDictCursor, Json, execute_values
from config import Config

EXTRACTED_PROFILE_KEYS = {
    'nickname',
    'code',
    'province',
    'city',
    'age',
    'height',
    'weight',
    'cup',
    'occupation',
    'is_virgin',
    'oral',
    'creampie',
    'condomless',
    'sm',
    'tattoo',
    'out_province',
    'overnight',
    'cohabitation',
    'monthly_allowance',
    'intro_fee',
    'contacts',
    'tags',
}


def _to_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on', 'ok'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return None


def _normalize_code(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r'[`\s]+', '', text)
    text = re.sub(r'[^A-Za-z0-9_-]', '', text)
    return text or None


def _is_empty_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def has_meaningful_extracted(extracted):
    """Returns True when extracted payload has at least one person field value."""
    if not isinstance(extracted, dict) or not extracted:
        return False
    for key in EXTRACTED_PROFILE_KEYS:
        if key not in extracted:
            continue
        if not _is_empty_value(extracted.get(key)):
            return True
    return False

class Database:
    def __init__(self):
        self.conn = psycopg2.connect(Config.DATABASE_URL)
        self.conn.autocommit = False

    def execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur

    def fetchone(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchone()

    def fetchall(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def try_acquire_lock(self, lock_key: int) -> bool:
        row = self.fetchone('SELECT pg_try_advisory_lock(%s)', (lock_key,))
        return bool(row[0]) if row else False

    def release_lock(self, lock_key: int):
        self.fetchone('SELECT pg_advisory_unlock(%s)', (lock_key,))

    def start_crawl_log(self, channel_id=None, owner_user_id=None) -> int:
        row = self.fetchone(
            """
            INSERT INTO crawl_logs (channel_id, owner_user_id, run_started_at, status)
            VALUES (%s, %s, NOW(), 'running')
            RETURNING id
            """,
            (channel_id, owner_user_id),
        )
        self.commit()
        return row[0]

    def bind_crawl_log_channel(self, log_id: int, channel_id: int):
        self.execute('UPDATE crawl_logs SET channel_id = %s WHERE id = %s', (channel_id, log_id))
        self.commit()

    def finish_crawl_log(self, log_id: int, status: str, processed: int, new_count: int, errors_count: int, error_details=None):
        sql = """
            UPDATE crawl_logs
            SET run_ended_at = NOW(),
                messages_processed = %s,
                messages_new = %s,
                errors_count = %s,
                error_details = %s,
                status = %s
            WHERE id = %s
        """
        self.execute(sql, (processed, new_count, errors_count, Json(error_details) if error_details else None, status, log_id))
        self.commit()

    def cleanup_stale_running_logs(self, max_age_minutes: int = 30) -> int:
        sql = """
            UPDATE crawl_logs
            SET run_ended_at = NOW(),
                status = 'failed',
                error_details = COALESCE(error_details, '[]'::jsonb) || to_jsonb(%s::text)
            WHERE status = 'running'
              AND run_ended_at IS NULL
              AND run_started_at < NOW() - (%s::text || ' minutes')::interval
            RETURNING id
        """
        marker = 'Marked failed by startup cleanup: previous run did not exit cleanly'
        rows = self.fetchall(sql, (marker, str(max_age_minutes)))
        self.commit()
        return len(rows or [])

    def insert_batch_messages(self, batch):
        if not batch:
            return
        sql = """
            INSERT INTO messages 
            (owner_user_id, channel_id, telegram_message_id, telegram_date, text_content, raw_json, 
             has_media, media_group_id, extracted_json, extract_confidence, status)
            VALUES %s
            ON CONFLICT (channel_id, telegram_message_id) DO NOTHING
        """
        values = [(
            b.get('owner_user_id'), b['channel_id'], b['telegram_message_id'], b['telegram_date'],
            b['text_content'], b['raw_json'], b['has_media'], b['media_group_id'],
            b.get('extracted_json'), b.get('extract_confidence'), b.get('status', 'pending')
        ) for b in batch]

        with self.conn.cursor() as cur:
            execute_values(cur, sql, values)
        self.conn.commit()

    def insert_media(self, message_id, file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, ocr_text, file_size, local_s3_url=None, local_thumb_url=None, owner_user_id=None):
        sql = """
            INSERT INTO media_files 
            (message_id, owner_user_id, telegram_file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, local_s3_url, local_thumb_url, ocr_text, file_size, processing_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'completed')
        """
        self.execute(sql, (message_id, owner_user_id, file_id, media_type, s3_key, s3_url, thumb_key, thumb_url, local_s3_url, local_thumb_url, ocr_text, file_size))
        self.commit()

    def upsert_channel(self, telegram_id, username, title, description, owner_user_id=None):
        sql = """
            INSERT INTO channels (telegram_id, username, title, description, owner_user_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                title = EXCLUDED.title,
                telegram_id = EXCLUDED.telegram_id,
                owner_user_id = COALESCE(channels.owner_user_id, EXCLUDED.owner_user_id)
            RETURNING id
        """
        row = self.fetchone(sql, (telegram_id, username, title, description, owner_user_id))
        self.commit()
        return row[0]

    def get_last_msg_id(self, username):
        row = self.fetchone(
            "SELECT last_crawled_msg_id FROM channels WHERE username = %s",
            (username,)
        )
        return row[0] if row else 0

    def update_checkpoint(self, username, last_id):
        self.execute(
            "UPDATE channels SET last_crawled_msg_id = %s WHERE username = %s",
            (last_id, username)
        )
        self.commit()

    def fetch_dedupe_candidates(self, channel_id: int, limit: int, owner_user_id=None):
        """最近入库的消息，供 LLM 判断是否与当前帖为同一人。"""
        sql = """
            SELECT id, telegram_message_id, text_content, extracted_json
            FROM messages
            WHERE channel_id = %s
              AND (%s::bigint IS NULL OR owner_user_id = %s)
            ORDER BY id DESC
            LIMIT %s
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (channel_id, owner_user_id, owner_user_id, limit))
            return cur.fetchall()

    def fetch_user_crawler_settings(self, user_id: int):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    tg_api_id,
                    tg_api_hash,
                    tg_phone,
                    tg_proxy_type,
                    tg_proxy_host,
                    tg_proxy_port,
                    tg_proxy_username,
                    tg_proxy_password,
                    COALESCE(target_channels, '{}'::text[]) AS target_channels
                FROM user_crawler_settings
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return cur.fetchone()

    def upsert_profile_from_extracted(self, message_id: int, extracted: dict, owner_user_id=None):
        if not has_meaningful_extracted(extracted):
            return False

        tags = extracted.get('tags')
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = None

        contacts = extracted.get('contacts')
        if isinstance(contacts, list):
            contacts = [str(c).strip() for c in contacts if str(c).strip()]
        else:
            contacts = None

        payload = {
            'display_nickname': (extracted.get('nickname') or '').strip() or None,
            'internal_code': _normalize_code(extracted.get('code')),
            'province': (extracted.get('province') or '').strip() or None,
            'city': (extracted.get('city') or '').strip() or None,
            'age': _to_int(extracted.get('age')),
            'height': _to_int(extracted.get('height')),
            'weight': _to_int(extracted.get('weight')),
            'cup_size': (extracted.get('cup') or '').strip() or None,
            'occupation': (extracted.get('occupation') or '').strip() or None,
            'is_virgin': _to_bool(extracted.get('is_virgin')),
            'oral_available': _to_bool(extracted.get('oral')),
            'creampie_available': _to_bool(extracted.get('creampie')),
            'condomless_available': _to_bool(extracted.get('condomless')),
            'sm_available': _to_bool(extracted.get('sm')),
            'has_tattoo': _to_bool(extracted.get('tattoo')),
            'out_province_available': _to_bool(extracted.get('out_province')),
            'overnight_available': _to_bool(extracted.get('overnight')),
            'cohabitation_available': _to_bool(extracted.get('cohabitation')),
            'monthly_allowance': _to_float(extracted.get('monthly_allowance')),
            'introduction_fee': _to_float(extracted.get('intro_fee')),
            'tags': tags,
            'contact_info': {'contacts': contacts} if contacts else None,
        }

        row = self.fetchone(
            'SELECT id FROM profiles WHERE message_id = %s ORDER BY id LIMIT 1',
            (message_id,),
        )

        if row:
            sql = """
                UPDATE profiles
                SET display_nickname = %s,
                    internal_code = %s,
                    province = %s,
                    city = %s,
                    owner_user_id = COALESCE(owner_user_id, %s),
                    age = %s,
                    height = %s,
                    weight = %s,
                    cup_size = %s,
                    occupation = %s,
                    is_virgin = %s,
                    oral_available = %s,
                    creampie_available = %s,
                    condomless_available = %s,
                    sm_available = %s,
                    has_tattoo = %s,
                    out_province_available = %s,
                    overnight_available = %s,
                    cohabitation_available = %s,
                    monthly_allowance = %s,
                    introduction_fee = %s,
                    tags = %s,
                    contact_info = %s,
                    updated_at = NOW()
                WHERE id = %s
            """
            self.execute(
                sql,
                (
                    payload['display_nickname'],
                    payload['internal_code'],
                    payload['province'],
                    payload['city'],
                    owner_user_id,
                    payload['age'],
                    payload['height'],
                    payload['weight'],
                    payload['cup_size'],
                    payload['occupation'],
                    payload['is_virgin'],
                    payload['oral_available'],
                    payload['creampie_available'],
                    payload['condomless_available'],
                    payload['sm_available'],
                    payload['has_tattoo'],
                    payload['out_province_available'],
                    payload['overnight_available'],
                    payload['cohabitation_available'],
                    payload['monthly_allowance'],
                    payload['introduction_fee'],
                    payload['tags'],
                    Json(payload['contact_info']) if payload['contact_info'] else None,
                    row[0],
                ),
        )
        self.execute(
            sql,
            (
                message_id,
                payload['display_nickname'],
                payload['internal_code'],
                payload['province'],
                payload['city'],
                owner_user_id,
                payload['age'],
                payload['height'],
                payload['weight'],
                payload['cup_size'],
                payload['occupation'],
                payload['is_virgin'],
                payload['oral_available'],
                payload['creampie_available'],
                payload['condomless_available'],
                payload['sm_available'],
                payload['has_tattoo'],
                payload['out_province_available'],
                payload['overnight_available'],
                payload['cohabitation_available'],
                payload['monthly_allowance'],
                payload['introduction_fee'],
                payload['tags'],
                Json(payload['contact_info']) if payload['contact_info'] else None,
            ),
        )
        self.commit()
        return True

    def _person_tags_from_extracted(self, extracted):
        tags = extracted.get('tags')
        if isinstance(tags, list):
            return [str(t).strip() for t in tags if str(t).strip()]
        return None

    def _person_contacts_from_extracted(self, extracted):
        contacts = extracted.get('contacts')
        if isinstance(contacts, list):
            return [str(c).strip() for c in contacts if str(c).strip()]
        return None

    def ensure_person(self, channel_id: int, code: Any, extracted: dict, owner_user_id=None):
        normalized_code = _normalize_code(code)
        if not normalized_code:
            row = self.fetchone(
                """INSERT INTO persons (owner_user_id, channel_id, display_nickname)
                   VALUES (%s, %s, %s)
                   RETURNING id""",
                (owner_user_id, channel_id, (extracted.get('nickname') or '').strip() or None),
            )
            self.commit()
            return row[0]

        row = self.fetchone(
            "SELECT id FROM persons WHERE channel_id = %s AND normalized_code = %s",
            (channel_id, normalized_code),
        )
        if row:
            self.execute(
                """UPDATE persons
                   SET profile_count = profile_count + 1,
                       last_seen_at = NOW(),
                       display_nickname = COALESCE(NULLIF(%s, ''), display_nickname)
                   WHERE id = %s""",
                ((extracted.get('nickname') or '').strip(), row[0]),
            )
            self.commit()
            return row[0]

        tags = self._person_tags_from_extracted(extracted)
        contacts = self._person_contacts_from_extracted(extracted)
        row = self.fetchone(
            """INSERT INTO persons
               (owner_user_id, channel_id, normalized_code, display_nickname,
                province, city, age, height, weight, cup_size, occupation,
                introduction_fee, monthly_allowance, tags, contact_info)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                owner_user_id, channel_id, normalized_code,
                (extracted.get('nickname') or '').strip() or None,
                (extracted.get('province') or '').strip() or None,
                (extracted.get('city') or '').strip() or None,
                _to_int(extracted.get('age')),
                _to_int(extracted.get('height')),
                _to_int(extracted.get('weight')),
                (extracted.get('cup') or '').strip() or None,
                (extracted.get('occupation') or '').strip() or None,
                _to_float(extracted.get('intro_fee')),
                _to_float(extracted.get('monthly_allowance')),
                tags,
                Json({'contacts': contacts}) if contacts else None,
            ),
        )
        self.commit()
        return row[0]

    def link_profile_to_person(self, profile_id: int, person_id: int):
        self.execute("UPDATE profiles SET person_id = %s WHERE id = %s", (person_id, profile_id))
        self.commit()

    def backfill_persons(self, limit: int = 500):
        rows = self.fetchall(
            """SELECT p.id AS profile_id, p.internal_code, p.message_id,
                      p.display_nickname, p.province, p.city, p.age, p.height, p.weight,
                      p.cup_size, p.occupation, p.introduction_fee, p.monthly_allowance,
                      p.tags, p.contact_info,
                      m.channel_id
               FROM profiles p
               LEFT JOIN messages m ON m.id = p.message_id
               WHERE p.person_id IS NULL AND m.channel_id IS NOT NULL
               LIMIT %s""",
            (limit,),
        )
        if not rows:
            return 0

        count = 0
        for r in rows:
            code = r['internal_code']
            extracted = {
                'nickname': r['display_nickname'],
                'code': code,
                'province': r['province'],
                'city': r['city'],
                'age': r['age'],
                'height': r['height'],
                'weight': r['weight'],
                'cup': r['cup_size'],
                'occupation': r['occupation'],
                'intro_fee': r['introduction_fee'],
                'monthly_allowance': r['monthly_allowance'],
                'tags': r['tags'],
                'contacts': (r['contact_info'] or {}).get('contacts') if r['contact_info'] else None,
            }
            person_id = self.ensure_person(
                r['channel_id'], code, extracted,
                owner_user_id=None,
            )
            self.link_profile_to_person(r['profile_id'], person_id)
            count += 1

        return count

        sql = """
            INSERT INTO profiles (
                message_id,
                display_nickname,
                internal_code,
                province,
                city,
                owner_user_id,
                age,
                height,
                weight,
                cup_size,
                occupation,
                is_virgin,
                oral_available,
                creampie_available,
                condomless_available,
                sm_available,
                has_tattoo,
                out_province_available,
                overnight_available,
                cohabitation_available,
                monthly_allowance,
                introduction_fee,
                tags,
                contact_info
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
        """
        self.execute(
            sql,
            (
                message_id,
                payload['display_nickname'],
                payload['internal_code'],
                payload['province'],
                payload['city'],
                owner_user_id,
                payload['age'],
                payload['height'],
                payload['weight'],
                payload['cup_size'],
                payload['occupation'],
                payload['is_virgin'],
                payload['oral_available'],
                payload['creampie_available'],
                payload['condomless_available'],
                payload['sm_available'],
                payload['has_tattoo'],
                payload['out_province_available'],
                payload['overnight_available'],
                payload['cohabitation_available'],
                payload['monthly_allowance'],
                payload['introduction_fee'],
                payload['tags'],
                Json(payload['contact_info']) if payload['contact_info'] else None,
            ),
        )
        self.commit()
        return True
