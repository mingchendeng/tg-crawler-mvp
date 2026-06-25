import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger('crawler')


def _normalize_code(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r'[`\s]+', '', text)
    text = re.sub(r'[^A-Za-z0-9_-]', '', text)
    return text or None


def _normalize_nickname(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Remove common decorators and whitespace
    text = re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9]', '', text)
    return text or None

_SYSTEM_PROMPT = """你是去重助手。同一 Telegram 频道里，用户可能多次发帖介绍同一个人（改文案、微调格式、重发带图等）。
根据「新帖」与若干「已入库」摘要，判断是否描述同一人。

规则：
- 若新帖与某条已入库信息指向同一真实人物（同一编号/昵称组合且内容明显为同一人不同版本），返回该条的 db_id。
- 若是不同的人、或无法判断，返回 null。
- 只输出一行 JSON，不要 Markdown，不要解释。格式：{"duplicate_of_db_id": <整数或 null>}"""


_DEFAULT_DEEPSEEK_CHAT_URL = 'https://api.deepseek.com/v1/chat/completions'
_DEFAULT_DEDUP_MODEL = 'deepseek-chat'


class LLMDeduper:
    """OpenAI 兼容 Chat Completions API；默认对接 DeepSeek，可改环境变量换其它网关。"""

    def __init__(self):
        self.enabled = os.getenv('DEDUP_LLM_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.api_url = os.getenv('DEDUP_LLM_API_URL', '').strip() or _DEFAULT_DEEPSEEK_CHAT_URL
        self.api_key = os.getenv('DEDUP_LLM_API_KEY', '').strip()
        self.model = os.getenv('DEDUP_LLM_MODEL', _DEFAULT_DEDUP_MODEL).strip()
        self.timeout = float(os.getenv('DEDUP_LLM_TIMEOUT_SEC', '60'))
        self.candidate_limit = max(1, int(os.getenv('DEDUP_CANDIDATE_LIMIT', '40')))
        self.max_text_chars = max(200, int(os.getenv('DEDUP_MAX_TEXT_CHARS', '1200')))
        self.max_field_chars = max(50, int(os.getenv('DEDUP_MAX_FIELD_JSON_CHARS', '800')))

    def is_configured(self) -> bool:
        return self.enabled and bool(self.api_url) and bool(self.api_key)

    def find_duplicate_by_code(self, db, channel_id: int, code: Any, owner_user_id: Optional[int] = None) -> Optional[int]:
        normalized = _normalize_code(code)
        if not normalized:
            return None
        row = db.fetchone(
            """
            SELECT id FROM messages
            WHERE channel_id = %s
              AND (%s::bigint IS NULL OR owner_user_id = %s)
              AND extracted_json->>'code' IS NOT NULL
              AND regexp_replace(regexp_replace(trim(extracted_json->>'code'), '[`\\s]+', '', 'g'), '[^A-Za-z0-9_-]', '', 'g') = %s
            LIMIT 1
            """,
            (channel_id, owner_user_id, owner_user_id, normalized),
        )
        return int(row[0]) if row else None

    def find_duplicate_by_nickname_code(
        self, db, channel_id: int, nickname: Any, code: Any, owner_user_id: Optional[int] = None
    ) -> Optional[int]:
        """Find duplicate by normalized nickname+code combination (or just nickname when code absent)."""
        norm_nick = _normalize_nickname(nickname)
        norm_code = _normalize_code(code)

        # If both nickname and code present, match either exact code or nickname+code combo
        if norm_nick and norm_code:
            # First try exact code match
            hit = self.find_duplicate_by_code(db, channel_id, code, owner_user_id=owner_user_id)
            if hit is not None:
                return hit
            # Then try same normalized nickname + code combo
            row = db.fetchone(
                """
                SELECT id FROM messages
                WHERE channel_id = %s
                  AND (%s::bigint IS NULL OR owner_user_id = %s)
                  AND regexp_replace(regexp_replace(trim(COALESCE(extracted_json->>'nickname', '')), '[`\\s]+', '', 'g'), '[^一-龥A-Za-z0-9]', '', 'g') = %s
                  AND regexp_replace(regexp_replace(trim(COALESCE(extracted_json->>'code', '')), '[`\\s]+', '', 'g'), '[^A-Za-z0-9_-]', '', 'g') = %s
                LIMIT 1
                """,
                (channel_id, owner_user_id, owner_user_id, norm_nick, norm_code),
            )
            return int(row[0]) if row else None

        # Only nickname: match by nickname (must be meaningful)
        if norm_nick:
            row = db.fetchone(
                """
                SELECT id FROM messages
                WHERE channel_id = %s
                  AND (%s::bigint IS NULL OR owner_user_id = %s)
                  AND regexp_replace(regexp_replace(trim(COALESCE(extracted_json->>'nickname', '')), '[`\\s]+', '', 'g'), '[^一-龥A-Za-z0-9]', '', 'g') = %s
                LIMIT 1
                """,
                (channel_id, owner_user_id, owner_user_id, norm_nick),
            )
            return int(row[0]) if row else None

        return None

    def _shrink_text(self, text: str) -> str:
        if not text:
            return ''
        t = text.strip()
        if len(t) <= self.max_text_chars:
            return t
        return t[: self.max_text_chars] + '…'

    def _shrink_extracted(self, data: Any) -> str:
        if not data:
            return '{}'
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return self._shrink_text(data)
        if not isinstance(data, dict):
            return str(data)[: self.max_field_chars]
        # 去掉内部统计字段，减小 token
        skip = {'_empty', '_raw_length', '_found_fields', '_expected_fields', 'confidence', '_status'}
        slim = {k: v for k, v in data.items() if k not in skip and not str(k).startswith('_')}
        s = json.dumps(slim, ensure_ascii=False, default=str)
        if len(s) <= self.max_field_chars:
            return s
        return s[: self.max_field_chars] + '…'

    def _build_user_payload(
        self, new_text: str, new_extracted: Dict[str, Any], candidates: List[Dict[str, Any]]
    ) -> str:
        items = []
        for c in candidates:
            cid = c.get('id')
            items.append(
                {
                    'db_id': cid,
                    'telegram_message_id': c.get('telegram_message_id'),
                    'text': self._shrink_text(c.get('text_content') or ''),
                    'fields': self._shrink_extracted(c.get('extracted_json')),
                }
            )
        return json.dumps(
            {
                'new_post': {
                    'text': self._shrink_text(new_text),
                    'fields': self._shrink_extracted(new_extracted),
                },
                'existing_posts': items,
            },
            ensure_ascii=False,
        )

    def _parse_llm_json(self, raw: str) -> Optional[int]:
        raw = (raw or '').strip()
        if not raw:
            return None
        obj = None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find('{'), raw.rfind('}')
            if start != -1 and end > start:
                try:
                    obj = json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return None
        if not isinstance(obj, dict):
            return None
        val = obj.get('duplicate_of_db_id')
        if val is None or val == 'null':
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    async def find_duplicate_db_id(
        self,
        new_text: str,
        new_extracted: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> Optional[int]:
        if not self.is_configured() or not candidates:
            return None

        valid_ids = {c['id'] for c in candidates if c.get('id') is not None}
        if not valid_ids:
            return None

        user_content = self._build_user_payload(new_text, new_extracted, candidates)
        body = {
            'model': self.model,
            'temperature': 0,
            'messages': [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': user_content},
            ],
        }
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(self.api_url, headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning('DEDUP LLM request failed (storing message): %s', e)
            return None

        try:
            content = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as e:
            logger.warning('DEDUP LLM unexpected response shape: %s', e)
            return None

        dup_id = self._parse_llm_json(content)
        if dup_id is not None and dup_id not in valid_ids:
            logger.warning('DEDUP LLM returned id=%s not in candidate set, ignoring', dup_id)
            return None
        return dup_id
