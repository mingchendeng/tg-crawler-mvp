#!/usr/bin/env python3
"""One-time migration: clean province data in profiles + persons tables.

First recovers original values from messages.extracted_json for rows
that were erroneously set to NULL, then applies corrected normalization.
"""

import os
import sys
import re

STANDARD_PROVINCES = {
    '北京', '天津', '上海', '重庆',
    '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东',
    '河南', '湖北', '湖南', '广东', '海南',
    '四川', '贵州', '云南', '陕西', '甘肃', '青海',
    '台湾', '内蒙古', '广西', '西藏', '宁夏', '新疆',
    '香港', '澳门',
    '跨省', '海外',
}

PROVINCE_MAP = {
    '上东': '山东', '安微': '安徽', '江浙': '跨省',
    '山东省': '山东', '河南省': '河南', '浙江省': '浙江', '湖南省': '湖南',
    '江苏省': '江苏', '安徽省': '安徽', '江西省': '江西', '四川省': '四川',
    '福建省': '福建', '云南省': '云南', '河北省': '河北', '贵州省': '贵州',
    '湖北省': '湖北', '陕西省': '陕西', '辽宁省': '辽宁', '吉林省': '吉林',
    '甘肃省': '甘肃', '青海省': '青海', '黑龙江省': '黑龙江',
    '广东省': '广东', '山西省': '山西', '海南省': '海南',
}

CITY_MAP = {
    '广州': '广东', '深圳': '广东', '东莞': '广东', '佛山': '广东',
    '珠海': '广东', '汕头': '广东', '惠州': '广东', '中山': '广东',
    '江门': '广东', '茂名': '广东', '肇庆': '广东', '湛江': '广东',
    '杭州': '浙江', '杭州拱墅': '浙江', '宁波': '浙江', '温州': '浙江',
    '绍兴': '浙江', '嘉兴': '浙江', '金华': '浙江', '湖州': '浙江',
    '台州': '浙江', '义乌': '浙江',
    '南京': '江苏', '苏州': '江苏', '无锡': '江苏', '常州': '江苏',
    '南通': '江苏', '徐州': '江苏', '扬州': '江苏', '镇江': '江苏',
    '盐城': '江苏', '淮安': '江苏',
    '成都': '四川', '绵阳': '四川', '宜宾': '四川',
    '武汉': '湖北', '宜昌': '湖北',
    '长沙': '湖南', '株洲': '湖南', '湘潭': '湖南',
    '福州': '福建', '厦门': '福建', '泉州': '福建',
    '合肥': '安徽', '芜湖': '安徽',
    '济南': '山东', '青岛': '山东', '山东青岛': '山东', '临沂': '山东',
    '淄博': '山东', '烟台': '山东',
    '哈尔滨': '黑龙江',
    '沈阳': '辽宁', '大连': '辽宁',
    '长春': '吉林',
    '石家庄': '河北', '唐山': '河北', '保定': '河北',
    '郑州': '河南', '洛阳': '河南',
    '太原': '山西',
    '西安': '陕西', '咸阳': '陕西',
    '兰州': '甘肃',
    '昆明': '云南', '大理': '云南',
    '贵阳': '贵州', '遵义': '贵州',
    '南宁': '广西', '桂林': '广西',
    '海口': '海南', '三亚': '海南',
    '呼和浩特': '内蒙古',
    '宁德': '福建',
    '西宁': '青海',
    '银川': '宁夏',
    '乌鲁木齐': '新疆',
    '拉萨': '西藏',
}

COUNTRY_NAMES = {'日本', '英国', '美国', '法国', '德国', '意大利', '西班牙',
    '葡萄牙', '澳大利亚', '加拿大', '新加坡', '马来西亚', '泰国', '韩国',
    '朝鲜', '印度', '越南', '吉隆坡', '迪拜'}

SEP_PAT = re.compile(r'[／/\s、]')


def normalize(raw):
    if not raw:
        return None
    v = raw.strip()
    if not v:
        return None

    # Strip prefixes
    for pfx in ['\U0001f3d9', '城市：', '城市:']:
        v = v.replace(pfx, '')
    v = v.strip()

    # Pure noise
    if v in {'可', '可以', '否', '\U0001f751'}:
        return None

    # Already a standard province name → pass through
    if v in STANDARD_PROVINCES:
        return v

    # Exact mapping (typos, full-form names → short form)
    if v in PROVINCE_MAP:
        return PROVINCE_MAP[v]

    # Multi-province / multi-country separators
    parts = [p.strip() for p in SEP_PAT.split(v) if p.strip()]
    if len(parts) > 1:
        mapped = []
        for part in parts:
            m = normalize(part)
            if m:
                mapped.append(m)
        unique = list(dict.fromkeys(mapped))
        if len(unique) == 1:
            return unique[0]
        if unique and all(p in COUNTRY_NAMES for p in unique):
            return '海外'
        return '跨省'

    # Strip parenthetical suffixes: 安徽（老家河南）→ 安徽
    no_suffix = re.sub(r'[（(].*?[）)]$', '', v).strip()
    # Strip YH-number suffixes: 湖北YH1023 → 湖北
    no_suffix = re.sub(r'[A-Za-z0-9]+$', '', no_suffix).strip()

    if no_suffix in STANDARD_PROVINCES:
        return no_suffix
    if no_suffix in PROVINCE_MAP:
        return PROVINCE_MAP[no_suffix]
    if no_suffix in CITY_MAP:
        return CITY_MAP[no_suffix]
    if v in CITY_MAP:
        return CITY_MAP[v]

    # Country check
    if v in COUNTRY_NAMES or no_suffix in COUNTRY_NAMES:
        return '海外'

    # HK/Macau/Taiwan substring
    if '香港' in v:
        return '香港'
    if '澳门' in v:
        return '澳门'
    if '台湾' in v:
        return '台湾'

    return None


def main():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        env_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DATABASE_URL='):
                        db_url = line.split('=', 1)[1].strip().strip("'\"")
                        break
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # === Step 1: Recover erroneously NULLed provinces from extracted_json ===
    print("=== Step 1: Recover NULL provinces from extracted_json ===")
    cur.execute("""
        UPDATE profiles p
        SET province = m.extracted_json->>'province'
        FROM messages m
        WHERE p.message_id = m.id
          AND p.province IS NULL
          AND m.extracted_json->>'province' IS NOT NULL
          AND m.extracted_json->>'province' != ''
    """)
    recovered = cur.rowcount
    conn.commit()
    print(f"Recovered {recovered} profiles.province from extracted_json")

    # === Step 2: Apply fixed normalization ===
    print("\n=== Step 2: Apply corrected normalization ===")
    for table in ('profiles', 'persons'):
        cur.execute(
            f"SELECT id, province FROM {table} WHERE province IS NOT NULL AND province != ''")
        rows = cur.fetchall()
        print(f"{table}: {len(rows)} rows with province")
        updates = []
        for r in rows:
            norm = normalize(r['province'])
            if norm != r['province']:
                updates.append((r['id'], r['province'], norm))
        if updates:
            for pid, old, new in updates:
                cur.execute(
                    f"UPDATE {table} SET province = %s WHERE id = %s", (new, pid))
            conn.commit()
            print(f"  Updated {len(updates)} rows")
            for pid, old, new in updates[:20]:
                print(f"    {old!r} -> {new!r}")
            if len(updates) > 20:
                print(f"    ... and {len(updates) - 20} more")
        else:
            print("  No updates needed")

    # === Step 3: Summary ===
    print("\n=== Step 3: Summary ===")
    cur.execute(
        "SELECT province, COUNT(*) AS cnt FROM profiles WHERE province IS NOT NULL GROUP BY province ORDER BY cnt DESC")
    for r in cur.fetchall():
        print(f"  {r['province']}: {r['cnt']}")
    nulls = cur.execute(
        "SELECT COUNT(*) AS cnt FROM profiles WHERE province IS NULL").fetchone()['cnt']
    print(f"  (NULL): {nulls}")

    cur.close()
    conn.close()
    print("\nDone!")


if __name__ == '__main__':
    main()
