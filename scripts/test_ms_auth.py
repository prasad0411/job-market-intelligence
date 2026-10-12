#!/usr/bin/env python3
"""Test Microsoft Graph authentication for Northeastern email."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_config import MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE, MS_SENDER_EMAIL
import msal, json

cache = msal.SerializableTokenCache()
if os.path.exists(MS_TOKEN_FILE):
    cache.deserialize(open(MS_TOKEN_FILE).read())

app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)

accounts = app.get_accounts()
result = None
if accounts:
    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    if result and "access_token" in result:
        print(f"✓ Already authenticated as {MS_SENDER_EMAIL}")
        print("  Token is valid — no browser sign-in needed.")
        sys.exit(0)

print("Opening browser for Microsoft sign-in...")
print(f"Sign in with: {MS_SENDER_EMAIL}")
print()
flow = app.initiate_device_flow(scopes=MS_SCOPES)
if "user_code" not in flow:
    print(f"Error: {flow.get('error_description')}")
    sys.exit(1)

print(flow["message"])
result = app.acquire_token_by_device_flow(flow)

if "access_token" in result:
    if cache.has_state_changed:
        open(MS_TOKEN_FILE, "w").write(cache.serialize())
    print(f"\n✓ Authentication successful!")
    print(f"  Sending as: {MS_SENDER_EMAIL}")
    print(f"  Token saved to: {MS_TOKEN_FILE}")
    print("\nYour outreach emails will now send from your Northeastern address.")
else:
    print(f"\n✗ Authentication failed: {result.get('error_description', result)}")
    sys.exit(1)
