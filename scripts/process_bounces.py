#!/usr/bin/env python3
"""
process_bounces.py — NDR processor for Job Hunt Tracker
Reads 'Failed Emails' folder, extracts bounced addresses,
feeds failures back to Brain, blacklists bad addresses,
and logs everything for self-learning.

Run via launchd every 30 min after send windows.
"""

# Socket ceiling, signal handling, child reaping, stall watchdog.
import scripts._resilient  # noqa: F401

import sys, os, re, json, datetime, logging, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_config import MS_SENDER_EMAIL, MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE
from outreach.brain import Brain
import requests as _req
from msal import PublicClientApplication, SerializableTokenCache
from aggregator.atomic_json import write_json as _atomic_write_json

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

_LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local")
BOUNCE_LOG = os.path.join(_LOCAL, "bounce_log.json")
PROCESSED_NDRS = os.path.join(_LOCAL, "processed_ndr_ids.json")

# ── auth ──────────────────────────────────────────────────────────────────────
def _get_token():
    cache = SerializableTokenCache()
    if os.path.exists(MS_TOKEN_FILE):
        cache.deserialize(open(MS_TOKEN_FILE).read())
    app = PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        raise Exception("MS token expired — run: python3 scripts/test_ms_auth.py")
    if cache.has_state_changed:
        # File lock prevents race condition when multiple jobs refresh token simultaneously
        import fcntl as _fcntl
        with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
            _fcntl.flock(_lf, _fcntl.LOCK_EX)
            open(MS_TOKEN_FILE, "w").write(cache.serialize())
            _fcntl.flock(_lf, _fcntl.LOCK_UN)
    return result["access_token"]

# ── folder helpers ─────────────────────────────────────────────────────────────
def _get_folder_id(token, name):
    resp = _req.get(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/mailFolders",
        headers={"Authorization": f"Bearer {token}"},
        params={"$top": 50}, timeout=10,
    )
    for f in resp.json().get("value", []):
        if f["displayName"].lower() == name.lower():
            return f["id"]
    return None

def _get_messages(token, folder_id, top=50):
    msgs, url = [], (
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}"
        f"/mailFolders/{folder_id}/messages"
        f"?$top={top}&$select=id,subject,from,body,receivedDateTime,toRecipients"
    )
    while url:
        for attempt in range(3):
            try:
                resp = _req.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                break
            except Exception as e:
                if attempt == 2:
                    log.warning(f"Graph API timeout after 3 attempts: {e}")
                    return msgs
                time.sleep(5 * (attempt + 1))
        if resp.status_code != 200:
            break
        data = resp.json()
        msgs.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        if len(msgs) >= 100:
            break
    return msgs

def _delete_message(token, msg_id):
    _req.delete(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    )

# ── NDR parsing ───────────────────────────────────────────────────────────────
def _extract_bounced_email(ndr_body_html, subject):
    """Extract the failed recipient email from an NDR message."""
    # Method 1: look for explicit "wasn't found" or "could not be delivered" patterns
    text = re.sub(r'<[^>]+>', ' ', ndr_body_html)  # strip HTML tags
    text = re.sub(r'\s+', ' ', text)

    # Pattern: "Your message to X couldn't be delivered"
    m = re.search(r'Your message to\s+([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9_.\-]+\.[a-zA-Z]{2,})', text)
    if m:
        return m.group(1).lower().strip()

    # Pattern: email address followed by "wasn't found"
    m = re.search(r'([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9_.\-]+\.[a-zA-Z]{2,})\s+wasn', text)
    if m:
        return m.group(1).lower().strip()

    # Pattern: "Recipient" followed by email
    m = re.search(r'[Rr]ecipient[:\s]+([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9_.\-]+\.[a-zA-Z]{2,})', text)
    if m:
        return m.group(1).lower().strip()

    # Method 2: extract from subject line "Undeliverable: ... | subject"
    # and cross-reference with sent_log
    return None

def _extract_subject_from_ndr(ndr_subject):
    """Extract original subject from NDR subject like 'Undeliverable: Original Subject'"""
    m = re.match(r'^(?:Undeliverable|Delivery has failed to these recipients|delivery failed)[:\s]+(.+)$',
                 ndr_subject, re.IGNORECASE)
    return m.group(1).strip() if m else None

def _load_bounce_log():
    try:
        return json.load(open(BOUNCE_LOG))
    except Exception:
        return {}

def _save_bounce_log(bl):
    _atomic_write_json(BOUNCE_LOG, bl)

def _load_processed():
    try:
        return set(json.load(open(PROCESSED_NDRS)))
    except Exception:
        return set()

def _save_processed(ids):
    _atomic_write_json(PROCESSED_NDRS, list(ids))

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"=== Bounce Processor started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    token = _get_token()
    brain = Brain.get()
    bounce_log = _load_bounce_log()
    processed_ids = _load_processed()

    failed_fid = _get_folder_id(token, "Failed Emails")
    if not failed_fid:
        print("ERROR: 'Failed Emails' folder not found")
        return

    msgs = _get_messages(token, failed_fid, top=100)
    print(f"Found {len(msgs)} messages in 'Failed Emails'")

    new_bounces = 0
    already_processed = 0

    for msg in msgs:
        msg_id = msg["id"]

        # Skip already processed
        if msg_id in processed_ids:
            already_processed += 1
            continue

        subject = msg.get("subject", "")
        body_html = msg.get("body", {}).get("content", "")
        received = msg.get("receivedDateTime", "")
        sender = msg.get("from", {}).get("emailAddress", {}).get("address", "")

        # Only process NDRs (from postmaster or Microsoft Exchange)
        is_ndr = (
            "postmaster" in sender.lower() or
            "microsoftexchange" in sender.lower() or
            "mailer-daemon" in sender.lower() or
            subject.lower().startswith("undeliverable") or
            "delivery" in subject.lower() and "fail" in subject.lower()
        )

        if not is_ndr:
            processed_ids.add(msg_id)
            continue

        # Extract bounced email
        bounced_email = _extract_bounced_email(body_html, subject)
        original_subject = _extract_subject_from_ndr(subject)

        if bounced_email:
            domain = bounced_email.split("@")[1]
            print(f"  ✗ Bounce: {bounced_email} | {original_subject[:50] if original_subject else subject[:50]}")

            # Feed failure to Brain — pattern + contact
            try:
                from scripts.send_scheduled import _email_to_pattern
                pattern = _email_to_pattern(bounced_email)
                brain.record_pattern_failure(domain, pattern)
                print(f"    → Brain: {domain} pattern '{pattern}' marked as failed")
            except Exception as be:
                log.debug(f"Brain pattern update failed: {be}")
            # Mark contact as bounced in company_contacts
            try:
                # Try to find which company this email belongs to
                # by checking domain against known company contacts
                contacts = brain._data.get("company_contacts", {})
                for co_key, roles in contacts.items():
                    for role, info in roles.items():
                        if info.get("email","").lower() == bounced_email.lower():
                            brain.mark_contact_bounced(co_key, role, bounced_email)
                            print(f"    → Brain: contact marked bounced for {co_key}")
                            break
            except Exception as ce:
                log.debug(f"Brain contact bounce failed: {ce}")

            # Log the bounce
            bounce_log[bounced_email] = {
                "domain": domain,
                "original_subject": original_subject or subject,
                "bounced_at": received,
                "ndr_from": sender,
                "processed_at": datetime.datetime.now().isoformat(),
            }
            new_bounces += 1
        else:
            print(f"  ? Could not extract email from NDR: {subject[:60]}")

        processed_ids.add(msg_id)

    # Save state — prune processed_ndr_ids older than 30 days to prevent unbounded growth
    # NDR IDs are Graph message IDs; messages older than 30 days are already deleted
    if len(processed_ids) > 500:
        # A set has no insertion order, so list(set)[-400:] kept an ARBITRARY
        # 400 ids. Recently-seen NDRs got dropped and re-processed, which
        # double-counted bounces — and two bounces marks a contact dead, so
        # one real bounce could blacklist a good contact. Keep the ids we saw
        # THIS run plus a bounded remainder instead.
        _seen_now = [m["id"] for m in msgs if m.get("id")]
        _rest = [i for i in processed_ids if i not in set(_seen_now)]
        processed_ids = set(_seen_now + _rest[:max(0, 400 - len(_seen_now))])
        log.info(f"Pruned processed_ndr_ids to {len(processed_ids)} entries (this run's kept)")
    _save_bounce_log(bounce_log)
    _save_processed(processed_ids)
    brain.save()

    print(f"\n{'─'*50}")
    print(f"New bounces processed : {new_bounces}")
    print(f"Already processed     : {already_processed}")
    print(f"Total in bounce_log   : {len(bounce_log)}")
    print(f"\nTop failing domains:")
    domain_counts = {}
    for email, info in bounce_log.items():
        d = info.get("domain", "")
        domain_counts[d] = domain_counts.get(d, 0) + 1
    for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {d}: {c} bounces")
    print(f"=== Done ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
