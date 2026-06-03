"""
Nordic-ON Session Keeper
Axe Global – Internal Tooling

Runs headlessly every 25 minutes (via Azure Container App Job scheduler).
Loads the saved session from Azure Blob Storage, navigates to the dashboard
to keep the token alive, then writes the refreshed session back to Blob.

No 2FA will ever be triggered as long as this job runs on schedule.

Environment variables required (set in Azure):
    AZURE_STORAGE_CONNECTION_STRING   – Blob Storage connection string
    BLOB_CONTAINER_NAME               – e.g. "axe-session"
    BLOB_NAME                         – e.g. "auth_state.json"
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime

from azure.storage.blob import BlobServiceClient
from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────
TARGET_URL         = "https://ncno.nordic-on.com/login/"
USERNAME           = "axebpo"
PASSWORD           = "Yay54641"
DASHBOARD_SELECTOR = "#OceanConsignmentListMain"

AZURE_CONN_STR     = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME     = os.environ.get("BLOB_CONTAINER_NAME", "axe-session")
BLOB_NAME          = os.environ.get("BLOB_NAME", "auth_state.json")
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)


# ── Blob helpers ──────────────────────────────────────────────────────────────

def download_session(tmp_path: str) -> bool:
    """Download auth_state.json from Blob to a local temp file. Returns True on success."""
    try:
        client = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
        blob   = client.get_blob_client(container=CONTAINER_NAME, blob=BLOB_NAME)
        with open(tmp_path, "wb") as f:
            f.write(blob.download_blob().readall())
        log(f"Session downloaded from Blob ({CONTAINER_NAME}/{BLOB_NAME}).")
        return True
    except Exception as e:
        log(f"[WARN] Could not download session from Blob: {e}")
        return False


def upload_session(tmp_path: str):
    """Upload the refreshed auth_state.json back to Blob."""
    try:
        client = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
        blob   = client.get_blob_client(container=CONTAINER_NAME, blob=BLOB_NAME)
        with open(tmp_path, "rb") as f:
            blob.upload_blob(f, overwrite=True)
        log(f"Refreshed session uploaded to Blob ({CONTAINER_NAME}/{BLOB_NAME}).")
    except Exception as e:
        log(f"[ERROR] Could not upload session to Blob: {e}")


# ── Core browser logic ────────────────────────────────────────────────────────

async def keep_session_alive():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    session_exists = download_session(tmp_path)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]   # required inside Docker/Azure
        )

        if session_exists:
            log("Creating context with saved session...")
            context = await browser.new_context(storage_state=tmp_path)
        else:
            log("No saved session. Creating fresh context...")
            context = await browser.new_context()

        page = await context.new_page()

        try:
            log(f"Navigating to {TARGET_URL} ...")
            await page.goto(TARGET_URL, timeout=60000)
            await page.wait_for_load_state("networkidle")

            # ── Check dashboard visible ───────────────────────────────────────
            try:
                on_dashboard = await page.locator(DASHBOARD_SELECTOR).is_visible(timeout=8000)
            except Exception:
                on_dashboard = False

            if on_dashboard:
                log("Dashboard confirmed. Session is alive.")
                await context.storage_state(path=tmp_path)
                upload_session(tmp_path)

            else:
                # Session expired – try credential login
                log("Not on dashboard. Attempting credential login...")
                try:
                    user_field = page.locator('input[id="username"], input[name="username"]').first
                    pass_field = page.locator('input[id="password"], input[name="password"]').first
                    btn        = page.locator(
                        'button[type="submit"], button:has-text("Log in"), button:has-text("Login")'
                    ).first

                    await user_field.wait_for(state="visible", timeout=10000)
                    await user_field.fill(USERNAME)
                    await pass_field.fill(PASSWORD)
                    await btn.click()
                    await page.wait_for_load_state("networkidle")
                    log("Credentials submitted. Waiting for dashboard...")

                    await page.wait_for_selector(DASHBOARD_SELECTOR, timeout=30000)
                    log("Dashboard reached after login.")
                    await context.storage_state(path=tmp_path)
                    upload_session(tmp_path)

                except Exception as e:
                    log(f"[ERROR] Login attempt failed: {e}")
                    log("2FA may be required. Run the local script manually once to refresh the session.")
                    sys.exit(1)

        finally:
            await browser.close()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    log("Job complete.")


if __name__ == "__main__":
    asyncio.run(keep_session_alive())
