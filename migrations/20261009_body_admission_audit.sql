-- Capture the initial stored body before later URL refreshes can change it.
-- This observes inserts only and does not change crawler behavior or existing pages.
CREATE TABLE IF NOT EXISTS page_admission_audit (
  page_id bigint PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL DEFAULT now(),
  original_fetched_at timestamptz NOT NULL,
  original_content_hash char(64) NOT NULL,
  original_body_chars integer NOT NULL,
  original_source_engines jsonb NOT NULL,
  original_language varchar(8),
  original_http_status integer NOT NULL
);

CREATE OR REPLACE FUNCTION capture_page_admission() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO page_admission_audit(
    page_id,original_fetched_at,original_content_hash,original_body_chars,
    original_source_engines,original_language,original_http_status
  ) VALUES (
    NEW.id,NEW.fetched_at,NEW.content_hash,char_length(NEW.content),
    NEW.source_engines,NEW.language,NEW.http_status
  ) ON CONFLICT (page_id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS pages_capture_admission ON pages;
CREATE TRIGGER pages_capture_admission AFTER INSERT ON pages
FOR EACH ROW EXECUTE FUNCTION capture_page_admission();
