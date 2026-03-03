-- Sentri Demo: Create a nearly-full tablespace for the demo to fix.
-- This script runs automatically on first container start (gvenzl/oracle-xe).

-- The APP_USER (sentri_demo) is created by the gvenzl image via env vars.
-- Grant DBA so Sentri can run ALTER TABLESPACE, gather stats, etc.
GRANT DBA TO sentri_demo;

-- Create a tablespace that's nearly full (50MB, no autoextend)
CREATE TABLESPACE DEMO_TS
  DATAFILE '/opt/oracle/oradata/FREE/FREEPDB1/demo_ts01.dbf'
  SIZE 50M AUTOEXTEND OFF;

-- Fill it with dummy data to ~90% usage
CREATE TABLE sentri_demo.filler (data VARCHAR2(4000))
  TABLESPACE DEMO_TS;

BEGIN
  FOR i IN 1..11000 LOOP
    INSERT INTO sentri_demo.filler VALUES (LPAD('X', 4000, 'X'));
    IF MOD(i, 1000) = 0 THEN COMMIT; END IF;
  END LOOP;
  COMMIT;
END;
/
