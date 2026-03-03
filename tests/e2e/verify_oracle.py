#!/usr/bin/env python
"""Verify Docker Oracle DB state after E2E tests.

Checks what Sentri actually did to the database.

Usage:
    python tests/e2e/verify_oracle.py
"""

from __future__ import annotations

import sys

try:
    import oracledb
except ImportError:
    print("oracledb not installed. Run: pip install oracledb")
    sys.exit(1)

# Oracle connection (matches config/sentri.yaml)
DSN = "localhost:1521/FREEPDB1"
USER = "system"
PASSWORD = "Oracle123"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def query(conn, sql):
    """Run a SELECT and return rows as list[dict]."""
    cur = conn.cursor()
    cur.execute(sql)
    if cur.description:
        cols = [c[0].lower() for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close()
        return rows
    cur.close()
    return []


def main():
    print(f"{BOLD}Oracle DB State Verification{RESET}")
    print(f"Connecting to {USER}@{DSN}...")

    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    print(f"{GREEN}Connected{RESET}\n")

    # 1. Database status
    print(f"{BOLD}1. Database Status{RESET}")
    rows = query(conn, "SELECT name, open_mode, database_role FROM v$database")
    for r in rows:
        print(f"   Name: {r['name']}, Mode: {r['open_mode']}, Role: {r['database_role']}")

    # 2. Tablespaces
    print(f"\n{BOLD}2. Tablespaces{RESET}")
    rows = query(
        conn,
        """
        SELECT tablespace_name, status, contents,
               ROUND(used_percent, 1) AS used_pct
        FROM dba_tablespace_usage_metrics m
        JOIN dba_tablespaces t USING (tablespace_name)
        ORDER BY used_percent DESC
    """,
    )
    print(f"   {'Tablespace':<25} {'Status':<10} {'Type':<12} {'Used %':>8}")
    print(f"   {'-' * 60}")
    for r in rows:
        pct = r.get("used_pct", 0) or 0
        color = RED if pct > 90 else YELLOW if pct > 75 else GREEN
        print(
            f"   {r['tablespace_name']:<25} {r['status']:<10} {r['contents']:<12} {color}{pct:>7.1f}%{RESET}"
        )

    # 3. Datafiles (look for any Sentri-created files)
    print(f"\n{BOLD}3. Recent Datafiles{RESET}")
    rows = query(
        conn,
        """
        SELECT tablespace_name, file_name,
               ROUND(bytes/1024/1024, 1) AS size_mb,
               autoextensible
        FROM dba_data_files
        ORDER BY file_id DESC
        FETCH FIRST 10 ROWS ONLY
    """,
    )
    for r in rows:
        print(
            f"   {r['tablespace_name']:<20} {r['size_mb']:>8.1f} MB  auto={r['autoextensible']}  {r['file_name']}"
        )

    # 4. Active sessions
    print(f"\n{BOLD}4. Active Sessions{RESET}")
    rows = query(
        conn,
        """
        SELECT sid, serial#, username, status, program,
               ROUND((SYSDATE - logon_time) * 24 * 60, 0) AS minutes
        FROM v$session
        WHERE type = 'USER' AND status = 'ACTIVE'
        ORDER BY logon_time
    """,
    )
    if rows:
        print(f"   {'SID':>5} {'Serial':>7} {'User':<15} {'Program':<25} {'Minutes':>8}")
        for r in rows:
            print(
                f"   {r['sid']:>5} {r['serial#']:>7} {(r['username'] or ''):.<15} {(r['program'] or ''):.<25} {r['minutes']:>8}"
            )
    else:
        print("   No active user sessions")

    # 5. Blocking sessions
    print(f"\n{BOLD}5. Blocking Sessions{RESET}")
    rows = query(
        conn,
        """
        SELECT s.sid, s.serial#, s.username,
               (SELECT COUNT(*) FROM v$session w WHERE w.blocking_session = s.sid) AS blocked_count
        FROM v$session s
        WHERE EXISTS (SELECT 1 FROM v$session w WHERE w.blocking_session = s.sid)
    """,
    )
    if rows:
        for r in rows:
            print(
                f"   {RED}SID {r['sid']} ({r['username']}) blocking {r['blocked_count']} sessions{RESET}"
            )
    else:
        print(f"   {GREEN}No blocking sessions{RESET}")

    # 6. Stale stats (tables not analyzed in 30+ days)
    print(f"\n{BOLD}6. Tables with Stale/Missing Stats{RESET}")
    rows = query(
        conn,
        """
        SELECT owner, table_name,
               ROUND(SYSDATE - last_analyzed) AS days_stale
        FROM dba_tables
        WHERE owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','WMSYS','DBSNMP',
                            'OUTLN','ORDSYS','ORDDATA','LBACSYS','DVSYS','AUDSYS')
        AND (last_analyzed IS NULL OR last_analyzed < SYSDATE - 30)
        AND num_rows IS NOT NULL
        ORDER BY days_stale DESC NULLS FIRST
        FETCH FIRST 10 ROWS ONLY
    """,
    )
    if rows:
        for r in rows:
            days = r["days_stale"] if r["days_stale"] else "NEVER"
            print(f"   {r['owner']}.{r['table_name']} — last analyzed: {days} days ago")
    else:
        print(f"   {GREEN}All tables have fresh stats{RESET}")

    conn.close()
    print(f"\n{GREEN}Verification complete.{RESET}")


if __name__ == "__main__":
    main()
