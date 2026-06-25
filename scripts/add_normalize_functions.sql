-- Create PostgreSQL functions to mirror Python _normalize_code and _normalize_nickname
CREATE OR REPLACE FUNCTION _normalize_code(text)
RETURNS text AS $$
DECLARE
    s text;
BEGIN
    s := trim($1);
    IF s IS NULL OR s = '' THEN
        RETURN NULL;
    END IF;
    s := regexp_replace(s, '[`\s]+', '', 'g');
    s := regexp_replace(s, '[^A-Za-z0-9_-]', '', 'g');
    RETURN NULLIF(s, '');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION _normalize_nickname(text)
RETURNS text AS $$
DECLARE
    s text;
BEGIN
    s := trim($1);
    IF s IS NULL OR s = '' THEN
        RETURN NULL;
    END IF;
    -- Keep CJK Unified Ideographs, CJK Extension A, and alphanumeric
    s := regexp_replace(s, '[^\u4e00-\u9fa5A-Za-z0-9]', '', 'g');
    RETURN NULLIF(s, '');
END;
$$ LANGUAGE plpgsql IMMUTABLE;
