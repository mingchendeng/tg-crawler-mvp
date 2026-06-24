-- 频道表
CREATE TABLE IF NOT EXISTS channels (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    telegram_id BIGINT UNIQUE,
    username VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(500),
    description TEXT,
    last_crawled_msg_id BIGINT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 消息表
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    channel_id BIGINT REFERENCES channels(id) ON DELETE CASCADE,
    telegram_message_id BIGINT NOT NULL,
    telegram_date TIMESTAMPTZ,
    text_content TEXT,
    raw_json JSONB NOT NULL,
    has_media BOOLEAN DEFAULT false,
    media_group_id BIGINT,
    extracted_json JSONB,
    extract_confidence DECIMAL(3,2),
    status VARCHAR(20) DEFAULT 'pending',
    review_status VARCHAR(20) DEFAULT 'pending',
    reviewer_id INTEGER,
    review_notes TEXT,
    review_time TIMESTAMPTZ,
    manual_tags TEXT[],
    is_flagged BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, telegram_date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_owner ON messages(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_review ON messages(review_status, extract_confidence);
CREATE INDEX IF NOT EXISTS idx_messages_flagged ON messages(is_flagged) WHERE is_flagged = true;
CREATE INDEX IF NOT EXISTS idx_messages_raw_gin ON messages USING GIN(raw_json);

-- 档案表（从 extracted_json 规范化，可手动修正）
CREATE TABLE IF NOT EXISTS profiles (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    message_id BIGINT REFERENCES messages(id) ON DELETE CASCADE,
    display_nickname VARCHAR(255),
    internal_code VARCHAR(50),
    province VARCHAR(100),
    city VARCHAR(100),
    age INTEGER,
    height INTEGER,
    weight INTEGER,
    cup_size VARCHAR(20),
    occupation VARCHAR(100),
    is_virgin BOOLEAN,
    oral_available BOOLEAN,
    creampie_available BOOLEAN,
    condomless_available BOOLEAN,
    sm_available BOOLEAN,
    has_tattoo BOOLEAN,
    out_province_available BOOLEAN,
    overnight_available BOOLEAN,
    cohabitation_available BOOLEAN,
    monthly_available_days INTEGER,
    period_date VARCHAR(100),
    meet_to_hotel BOOLEAN DEFAULT true,
    monthly_allowance DECIMAL(12,2),
    allowance_currency VARCHAR(10) DEFAULT 'CNY',
    introduction_fee DECIMAL(12,2),
    fee_currency VARCHAR(10) DEFAULT 'CNY',
    fee_agent_name VARCHAR(255),
    motivation TEXT,
    self_introduction TEXT,
    deal_breakers TEXT,
    tags TEXT[],
    verification_bot_link TEXT,
    contact_info JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_province_city ON profiles(province, city);
CREATE INDEX IF NOT EXISTS idx_profiles_owner ON profiles(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_profiles_age ON profiles(age);
CREATE INDEX IF NOT EXISTS idx_profiles_fee ON profiles(introduction_fee);

-- 人物表（按编号归并，一人一记录）
CREATE TABLE IF NOT EXISTS persons (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    channel_id BIGINT REFERENCES channels(id) ON DELETE CASCADE,
    normalized_code VARCHAR(50),
    display_nickname VARCHAR(255),
    province VARCHAR(100),
    city VARCHAR(100),
    age INTEGER,
    height INTEGER,
    weight INTEGER,
    cup_size VARCHAR(20),
    occupation VARCHAR(100),
    introduction_fee DECIMAL(12,2),
    monthly_allowance DECIMAL(12,2),
    tags TEXT[],
    contact_info JSONB,
    profile_count INTEGER DEFAULT 1,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persons_channel_code ON persons(channel_id, normalized_code) WHERE normalized_code IS NOT NULL;

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS person_id BIGINT REFERENCES persons(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_profiles_person ON profiles(person_id);

-- 媒体文件表
CREATE TABLE IF NOT EXISTS media_files (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    message_id BIGINT REFERENCES messages(id) ON DELETE CASCADE,
    telegram_file_id VARCHAR(255),
    file_unique_id VARCHAR(255),
    media_type VARCHAR(50) NOT NULL,
    mime_type VARCHAR(100),
    file_size BIGINT,
    width INTEGER,
    height INTEGER,
    s3_bucket VARCHAR(100),
    s3_key VARCHAR(500),
    s3_url TEXT,
    thumb_key VARCHAR(500),
    thumb_url TEXT,
    local_s3_url TEXT,
    local_thumb_url TEXT,
    local_path VARCHAR(500),
    ocr_text TEXT,
    is_nsfw BOOLEAN,
    face_detected BOOLEAN,
    processing_status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_message ON media_files(message_id);
CREATE INDEX IF NOT EXISTS idx_media_owner ON media_files(owner_user_id);

-- 审核人员表
CREATE TABLE IF NOT EXISTS reviewers (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'user',
    full_name VARCHAR(255),
    email VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    must_change_password BOOLEAN DEFAULT false,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_username_ci ON reviewers (LOWER(username));

CREATE TABLE IF NOT EXISTS user_crawler_settings (
    user_id BIGINT PRIMARY KEY REFERENCES reviewers(id) ON DELETE CASCADE,
    tg_api_id BIGINT,
    tg_api_hash TEXT,
    tg_phone VARCHAR(64),
    tg_proxy_type VARCHAR(20),
    tg_proxy_host VARCHAR(255),
    tg_proxy_port INTEGER,
    tg_proxy_username VARCHAR(255),
    tg_proxy_password VARCHAR(255),
    target_channels TEXT[] DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES messages(id),
    reviewer_id INTEGER REFERENCES reviewers(id),
    action VARCHAR(50) NOT NULL,
    old_values JSONB,
    new_values JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 采集日志
CREATE TABLE IF NOT EXISTS crawl_logs (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT,
    channel_id BIGINT REFERENCES channels(id),
    run_started_at TIMESTAMPTZ,
    run_ended_at TIMESTAMPTZ,
    messages_processed INTEGER DEFAULT 0,
    messages_new INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    error_details JSONB,
    status VARCHAR(50) DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS idx_crawl_logs_owner ON crawl_logs(owner_user_id);

-- 默认管理员由 web 服务启动时写入（见 web/main.py init_admin），避免在 SQL 中硬编码无效的密码哈希。
