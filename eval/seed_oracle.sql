-- =============================================================
-- Sentri Evaluation: Oracle Seed Script
-- Run once against Docker Oracle XE (system@localhost:1521/FREEPDB1)
-- =============================================================

-- P1: Tablespace DEMO_TS already created by docker/oracle-init/01_setup_demo.sql
--     (~50MB, ~90% full with sentri_demo.filler table)
--     No additional seed needed for P1.

-- ---------------------------------------------------------
-- P2: Stale stats — ORDERS table under sentri_demo schema
-- ---------------------------------------------------------
BEGIN
    EXECUTE IMMEDIATE 'DROP TABLE sentri_demo.orders PURGE';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

CREATE TABLE sentri_demo.orders (
    order_id    NUMBER        PRIMARY KEY,
    customer_id NUMBER        NOT NULL,
    order_date  DATE          NOT NULL,
    total       NUMBER(10,2)  NOT NULL,
    status      VARCHAR2(20)  DEFAULT 'PENDING'
) TABLESPACE DEMO_TS;

-- Insert 500 rows
BEGIN
    FOR i IN 1..500 LOOP
        INSERT INTO sentri_demo.orders (order_id, customer_id, order_date, total, status)
        VALUES (
            i,
            MOD(i, 50) + 1,
            SYSDATE - MOD(i, 90),
            ROUND(DBMS_RANDOM.VALUE(10, 5000), 2),
            CASE MOD(i, 5)
                WHEN 0 THEN 'COMPLETED'
                WHEN 1 THEN 'PENDING'
                WHEN 2 THEN 'SHIPPED'
                WHEN 3 THEN 'CANCELLED'
                ELSE 'PROCESSING'
            END
        );
    END LOOP;
    COMMIT;
END;
/

-- Delete stats to make them "stale"
BEGIN
    DBMS_STATS.DELETE_TABLE_STATS('SENTRI_DEMO', 'ORDERS');
END;
/

-- ---------------------------------------------------------
-- P3: Blocking session test table
-- ---------------------------------------------------------
BEGIN
    EXECUTE IMMEDIATE 'DROP TABLE sentri_demo.block_test PURGE';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -942 THEN RAISE; END IF;
END;
/

CREATE TABLE sentri_demo.block_test (
    id  NUMBER PRIMARY KEY,
    val VARCHAR2(100)
);

INSERT INTO sentri_demo.block_test VALUES (1, 'initial');
COMMIT;

-- Verify
SELECT 'P1: DEMO_TS' AS incident, COUNT(*) AS rows_count FROM sentri_demo.filler
UNION ALL
SELECT 'P2: ORDERS', COUNT(*) FROM sentri_demo.orders
UNION ALL
SELECT 'P3: BLOCK_TEST', COUNT(*) FROM sentri_demo.block_test;
