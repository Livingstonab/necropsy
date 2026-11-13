-- 0002_inscription.sql
--
-- The tombstone inscription: one line a visitor carves onto an exhibit, signed
-- "— you". Nullable, because most exhibits have none. It is the only
-- visitor-authored field in the schema, and it is per exhibit, so it lives on
-- the exhibits row rather than the cached analysis (which is byte-identical to
-- what GitHub yielded and must stay that way, E3).
--
-- Stored full; truncated only at display. Repo-derived data this is not — it is
-- the visitor's own words — but it is still rendered escaped (E4), because it
-- arrives over HTTP.

ALTER TABLE exhibits ADD COLUMN user_inscription TEXT;
ALTER TABLE exhibits ADD COLUMN inscribed_at TEXT;
