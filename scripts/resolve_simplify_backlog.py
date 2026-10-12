#!/usr/bin/env python3
"""
One-time script: resolve existing Simplify wrapper URLs in Valid Entries.
Re-running is a safe no-op (tracked in Brain).
    python3 scripts/resolve_simplify_backlog.py
"""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

def main():
    from outreach.brain import Brain
    b = Brain.get()
    if b._data.get("simplify_backlog_done"):
        log.info("Simplify backlog already resolved. Nothing to do.")
        return
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    from aggregator.config import SHEETS_CREDS_FILE, SHEET_NAME, WORKSHEET_NAME
    from aggregator.extractors import SimplifyRedirectResolver
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS_FILE, scope)
    gc = gspread.authorize(creds)
    ws = gc.open(SHEET_NAME).worksheet(WORKSHEET_NAME)
    rows = ws.get_all_values()
    updates = []
    resolved = 0
    skipped = 0
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= 5:
            continue
        url = row[5].strip()
        if "simplify.jobs/p/" not in url.lower():
            continue
        log.info(f"  Row {i}: resolving {url[:60]}")
        try:
            SimplifyRedirectResolver.resolve.cache_clear()
            result_url, success = SimplifyRedirectResolver.resolve(url)
            if success and result_url and result_url != url:
                updates.append({"range": f"F{i}", "values": [[result_url]]})
                resolved += 1
                log.info(f"    → {result_url[:70]}")
            else:
                skipped += 1
                log.info(f"    ✗ Could not resolve")
        except Exception as e:
            log.warning(f"    ✗ Error: {e}")
            skipped += 1
        time.sleep(0.5)
    if updates:
        for chunk in range(0, len(updates), 50):
            ws.batch_update(updates[chunk:chunk+50], value_input_option="USER_ENTERED")
            time.sleep(1)
        log.info(f"Updated {resolved} URLs in sheet")
    b._data["simplify_backlog_done"] = True
    b._data["simplify_backlog_resolved"] = resolved
    b._data["simplify_backlog_ts"] = time.time()
    b.save()
    log.info(f"Done: {resolved} resolved, {skipped} skipped. Won't run again.")

if __name__ == "__main__":
    main()
