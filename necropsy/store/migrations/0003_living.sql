-- 0003_living.sql
--
-- A repository that is still being committed to is alive, not dead (P6). It gets
-- an exhibit row so its check-up page works and a repeat paste is instant, but
-- it must never hang on the graveyard wall: a living project does not belong in
-- a museum of the dead. This flag is how every listing tells them apart.
--
-- Defaults to 0, so every existing exhibit stays on the wall.

ALTER TABLE exhibits ADD COLUMN is_living INTEGER NOT NULL DEFAULT 0;
