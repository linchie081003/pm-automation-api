-- Idempotent: safe to run more than once (resets pdc password to 'pdc').

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pdc') THEN
    CREATE ROLE pdc LOGIN PASSWORD 'pdc';
  ELSE
    ALTER ROLE pdc WITH LOGIN PASSWORD 'pdc';
  END IF;
END
$$;

SELECT 'CREATE DATABASE pdc OWNER pdc'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pdc')\gexec

GRANT ALL PRIVILEGES ON DATABASE pdc TO pdc;
