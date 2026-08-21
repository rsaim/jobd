-- Reverse of 0001. Drop in dependency order, then the extension the up
-- migration created — M3 gate 1 is that down leaves an *empty* database, not a
-- nearly-empty one.
--
-- Indexes and constraints go with their tables; naming them here would only
-- create a second place to forget something.

DROP TABLE IF EXISTS stage_event;
DROP TABLE IF EXISTS message;
DROP TABLE IF EXISTS application;
DROP TABLE IF EXISTS contact_company;
DROP TABLE IF EXISTS contact_identity;
DROP TABLE IF EXISTS contact;
DROP TABLE IF EXISTS company_alias;
DROP TABLE IF EXISTS company;

-- IF EXISTS, because a database that had pgvector before jobd arrived will
-- have had `CREATE EXTENSION IF NOT EXISTS` do nothing on the way up. Dropping
-- it here is still the right call: this migration is the only reason jobd
-- assumes it is present.
DROP EXTENSION IF EXISTS vector;
