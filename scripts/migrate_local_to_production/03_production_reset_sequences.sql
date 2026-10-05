-- Set ulang semua sequence serial/identity di public setelah import manual/pg_dump.
-- Jalankan di production SETELAH pdc_local_data.sql sukses.

DO $$
DECLARE
  r RECORD;
  max_val BIGINT;
  seq regclass;
BEGIN
  FOR r IN
    SELECT
      n.nspname AS schema_name,
      c.relname AS table_name,
      a.attname AS column_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND pg_get_serial_sequence(
            quote_ident(n.nspname) || '.' || quote_ident(c.relname),
            a.attname
          ) IS NOT NULL
  LOOP
    seq := pg_get_serial_sequence(
      quote_ident(r.schema_name) || '.' || quote_ident(r.table_name),
      r.column_name
    )::regclass;

    EXECUTE format(
      'SELECT COALESCE(MAX(%I), 0) FROM %I.%I',
      r.column_name,
      r.schema_name,
      r.table_name
    )
    INTO max_val;

    IF max_val > 0 THEN
      PERFORM setval(seq, max_val, true);
    ELSE
      PERFORM setval(seq, 1, false);
    END IF;

    RAISE NOTICE 'Sequence % for %.% → %',
      seq, r.table_name, r.column_name, max_val;
  END LOOP;
END $$;
