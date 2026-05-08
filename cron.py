#!/usr/bin/env python3
"""
Daily cron sync — runs automatically every morning at 8 AM Bangkok (01:00 UTC).
Only syncs:
  1. Jira progress → Lark
  2. Lark start/end dates → Jira

Run manually: python3 cron.py
"""
import os
import json
from datetime import datetime
from pathlib import Path
import sync_engine

SCRIPT_DIR = Path(__file__).parent
LOG_FILE   = SCRIPT_DIR / "cron_log.json"


def load_cfg() -> dict:
    return {
        "JIRA_EMAIL":      os.environ["JIRA_EMAIL"],
        "JIRA_TOKEN":      os.environ["JIRA_TOKEN"],
        "JIRA_DOMAIN":     os.environ["JIRA_DOMAIN"],
        "JIRA_PROJECT":    os.environ["JIRA_PROJECT"],
        "LARK_APP_ID":     os.environ["LARK_APP_ID"],
        "LARK_APP_SECRET": os.environ["LARK_APP_SECRET"],
        "LARK_BASE_TOKEN": os.environ["LARK_BASE_TOKEN"],
        "LARK_TABLE_ID":   os.environ["LARK_TABLE_ID"],
    }


def run():
    cfg = load_cfg()
    ran_at = datetime.now().isoformat()
    print(f"[{ran_at}] Cron sync starting...")

    results = {}

    # Op 1: Jira progress → Lark
    try:
        r = sync_engine.sync_jira_progress_assignee_to_lark(cfg)
        results["jira_progress_to_lark"] = r.summary()
        print(f"  ✅ Jira progress → Lark: {r.updated} updated, {r.skipped} skipped, {len(r.errors)} errors")
    except Exception as e:
        results["jira_progress_to_lark"] = {"error": str(e)}
        print(f"  ❌ Jira progress → Lark failed: {e}")

    # Op 2: Lark dates → Jira
    try:
        r = sync_engine.sync_lark_dates_to_jira(cfg)
        results["lark_dates_to_jira"] = r.summary()
        print(f"  ✅ Lark dates → Jira: {r.updated} updated, {r.skipped} skipped, {len(r.errors)} errors")
    except Exception as e:
        results["lark_dates_to_jira"] = {"error": str(e)}
        print(f"  ❌ Lark dates → Jira failed: {e}")

    # Save log
    log = {
        "last_run": ran_at,
        "results": results,
        "status": "error" if any("error" in v for v in results.values()) else "ok",
    }
    LOG_FILE.write_text(json.dumps(log, indent=2))
    print(f"[{datetime.now().isoformat()}] Cron sync complete. Log saved.")
    return log


if __name__ == "__main__":
    run()
