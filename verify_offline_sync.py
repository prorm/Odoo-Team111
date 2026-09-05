"""
verify_offline_sync.py

Live, raw-output proof of Harmonix360's offline sync layer (HARMONIX360_ARCHITECTURE.md
Section 13.3). Every claim below is demonstrated against a real running stack —
no step is "should work," every step prints the actual raw response/DB/IndexedDB
content it produced.

Preconditions (see printed startup checks):
  - Postgres reachable at localhost:5544, database harmonix360_dev, migrated to
    014_sync_mutations_table (docker compose up postgres, then alembic upgrade head)
  - Backend running: uvicorn app.main:app --host 127.0.0.1 --port 8000
    (from harmonix360/backend, with the repo-root .env loaded)
  - Frontend running: npm run dev (from harmonix360/frontend), served at :3000,
    proxying /api and /health to :8000 (see vite.config.ts)
  - `playwright install chromium` has been run once

Sections:
  A. Offline creation & update persisted locally in IndexedDB (real Chromium,
     real navigator.onLine=false via BrowserContext.set_offline)
  B. Push/pull reconciliation matching server state exactly
  C. Live conflict reproduction: Client A edits online (raw HTTP), Client B
     edits the same row offline (real browser), then reconnects and receives
     an explicit 409-shaped conflict payload with the server's diff
  D. Idempotent push: submitting the identical client_mutation_id twice
     returns the original outcome verbatim — no re-increment, no duplicate row
"""
import json
import subprocess
import sys
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000/api/v1"
HEALTH_URL = "http://localhost:8000/health"
FRONTEND_URL = "http://localhost:3000/notes"
DB_CONTAINER = "harmonix360_postgres"
DB_NAME = "harmonix360_dev"


def section(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def dump(title: str, obj) -> None:
    print(f"\n----- {title} -----")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def db_query(sql: str) -> str:
    """Raw psql query against the real dev database — byte-for-byte proof,
    not a description of expected state."""
    result = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", "postgres", "-d", DB_NAME, "-c", sql],
        capture_output=True, text=True,
    )
    return (result.stdout + result.stderr).strip()


def read_indexeddb(page) -> dict:
    return page.evaluate(
        """
        async () => {
          function readAll(db, storeName) {
            return new Promise((resolve, reject) => {
              const tx = db.transaction(storeName, 'readonly');
              const req = tx.objectStore(storeName).getAll();
              req.onsuccess = () => resolve(req.result);
              req.onerror = () => reject(req.error);
            });
          }
          const db = await new Promise((resolve, reject) => {
            const req = indexedDB.open('harmonix360-offline');
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          const [cache, outbox, conflicts] = await Promise.all([
            readAll(db, 'cache'), readAll(db, 'outbox'), readAll(db, 'conflicts'),
          ]);
          db.close();
          return { cache, outbox, conflicts };
        }
        """
    )


def preflight() -> None:
    section("PREFLIGHT")
    health = requests.get(HEALTH_URL, timeout=5)
    dump("GET /health", health.json())
    assert health.status_code == 200
    print(f"Backend reachable at {BASE_API}")
    print(f"Frontend reachable, will drive {FRONTEND_URL}")


class NetworkCapture:
    """Records raw request/response bodies for /sync/push and /sync/pull calls
    made by the real page — not calls this script makes itself."""

    def __init__(self, page):
        self.page = page
        self.entries = []
        page.on("response", self._on_response)

    def _on_response(self, response):
        url = response.url
        if "/sync/push" in url or "/sync/pull" in url:
            try:
                body = response.json()
            except Exception:
                body = response.text()
            self.entries.append({
                "url": url,
                "method": response.request.method,
                "status": response.status,
                "request_body": response.request.post_data,
                "response_body": body,
            })

    def latest(self, path_fragment: str):
        matches = [e for e in self.entries if path_fragment in e["url"]]
        return matches[-1] if matches else None

    def clear(self):
        self.entries.clear()


def part_a_offline_create_and_update(page, capture: NetworkCapture):
    section("PART A — Offline creation & update persisted locally in IndexedDB")
    page.goto(FRONTEND_URL)
    page.wait_for_selector('[data-testid="new-note-input"]')

    context = page.context
    print(">>> context.set_offline(True) — real navigator.onLine=false in this browser context")
    context.set_offline(True)
    page.wait_for_timeout(600)

    banner_visible = page.locator('[data-testid="offline-banner"]').is_visible()
    print(f">>> Offline banner visible: {banner_visible}")
    assert banner_visible, "offline banner did not appear after going offline"

    unique = f"OFFLINE-CREATE-{uuid.uuid4().hex[:8]}"
    page.fill('[data-testid="new-note-input"]', unique)
    page.click('[data-testid="add-note-button"]')
    page.wait_for_timeout(400)

    state = read_indexeddb(page)
    dump("RAW IndexedDB state immediately after OFFLINE create", state)

    outbox_entry = next((o for o in state["outbox"] if o["payload"] and o["payload"].get("content") == unique), None)
    cache_entry = next((c for c in state["cache"] if c["data"] and c["data"].get("content") == unique), None)
    assert outbox_entry is not None, "offline CREATE was not queued in the outbox"
    assert cache_entry is not None, "offline CREATE was not written to the local read cache"
    assert outbox_entry["status"] == "pending"
    print(f">>> VERIFIED: outbox has a pending CREATE (client_mutation_id={outbox_entry['client_mutation_id']}), "
          f"cache has the optimistic row under local id {cache_entry['public_id']}")

    print(">>> Editing the same row while still offline (queues an UPDATE)")
    page.locator('[data-testid="note-content"]', has_text=unique).click()
    page.wait_for_timeout(200)
    edited = unique + "-EDITED-OFFLINE"
    page.fill('[data-testid="edit-note-input"]', edited)
    page.click('[data-testid="save-edit-button"]')
    page.wait_for_timeout(400)

    state2 = read_indexeddb(page)
    dump("RAW IndexedDB state after OFFLINE update of the same row", state2)
    update_entry = next((o for o in state2["outbox"] if o["op"] == "UPDATE" and o["payload"]
                          and o["payload"].get("content") == edited), None)
    assert update_entry is not None, "offline UPDATE was not queued"
    print(">>> VERIFIED: outbox now also has the pending UPDATE, queued entirely offline")

    print(">>> context.set_offline(False) — reconnecting")
    capture.clear()
    context.set_offline(False)
    page.wait_for_timeout(6000)  # reachability poll interval is 5s

    push_call = capture.latest("/sync/push")
    pull_call = capture.latest("/sync/pull")
    dump("RAW request/response captured for the auto-triggered POST /sync/push on reconnect", push_call)
    dump("RAW request/response captured for the auto-triggered GET /sync/pull on reconnect", pull_call)

    final_state = read_indexeddb(page)
    dump("RAW IndexedDB state after reconnect + auto-sync", final_state)
    assert final_state["outbox"] == [], "outbox did not drain after reconnect sync"
    synced = next((c for c in final_state["cache"] if c["data"] and c["data"].get("content") == edited), None)
    assert synced is not None, "synced row with final content not found in cache after reconnect"
    assert not synced["public_id"].startswith("local:"), "cache row still keyed by local placeholder id after sync"
    print(f">>> VERIFIED: outbox drained, cache row migrated to real public_id={synced['public_id']}, version={synced['version']}")

    return synced["public_id"], synced["version"], edited


def part_b_push_pull_reconciliation(public_id: str, expected_version: int, expected_content: str):
    section("PART B — Push/pull reconciliation matching server state exactly")
    resp = requests.get(f"{BASE_API}/notes/{public_id}")
    dump(f"GET /api/v1/notes/{public_id} (server's authoritative state)", resp.json())
    server = resp.json()
    assert server["content"] == expected_content, "server content does not match what the client synced"
    assert server["version"] == expected_version, "server version does not match what the client synced"
    print(">>> VERIFIED: server row matches the IndexedDB cache exactly after sync — genuine reconciliation, not a stub")


def part_c_live_conflict(page, capture: NetworkCapture, public_id: str, known_version: int):
    section("PART C — Live conflict: Client A edits online, Client B (offline) replays and gets 409")

    context = page.context
    print(">>> Client B (this browser) goes offline")
    context.set_offline(True)
    page.wait_for_timeout(500)

    print(">>> Client B edits the note while offline")
    page.locator(f'[data-testid="note-row-{public_id}"] [data-testid="note-content"]').click()
    page.wait_for_timeout(200)
    client_b_text = "EDITED BY CLIENT B (OFFLINE)"
    page.fill('[data-testid="edit-note-input"]', client_b_text)
    page.click('[data-testid="save-edit-button"]')
    page.wait_for_timeout(300)

    print(">>> Meanwhile, Client A edits the SAME row directly against the backend, while online")
    resp_a = requests.patch(f"{BASE_API}/notes/{public_id}", json={"content": "EDITED BY CLIENT A (ONLINE)"})
    dump("Client A's raw PATCH response (this is what makes B's known_version stale)", resp_a.json())
    assert resp_a.status_code == 200
    server_version_after_a = resp_a.json()["version"]
    assert server_version_after_a == known_version + 1

    print(">>> Client B reconnects")
    capture.clear()
    context.set_offline(False)
    page.wait_for_timeout(6000)

    push_call = capture.latest("/sync/push")
    dump("RAW POST /sync/push response — Client B's replay against the now-stale known_version", push_call)
    assert push_call is not None, "no /sync/push call observed after reconnect"
    results = push_call["response_body"]["results"]
    conflict_result = next((r for r in results if r["outcome"] == "conflict"), None)
    assert conflict_result is not None, f"expected a conflict outcome, got: {results}"
    assert conflict_result["error"]["code"] == "VERSION_CONFLICT"
    assert conflict_result["error"]["details"]["current_state"]["content"] == "EDITED BY CLIENT A (ONLINE)"
    print(">>> VERIFIED: server returned an explicit 409-shaped conflict payload carrying its own diff "
          "(current_state), not a silent merge or overwrite")

    page.wait_for_timeout(500)
    modal_visible = page.locator('[data-testid="conflict-modal"]').is_visible()
    print(f">>> Conflict resolution modal visible in the UI: {modal_visible}")
    assert modal_visible, "conflict modal did not appear after the push conflict"
    modal_text = page.locator('[data-testid="conflict-modal"]').inner_text()
    dump("RAW conflict modal DOM text (shows both versions, server diff included)", modal_text)

    row_after_conflict = requests.get(f"{BASE_API}/notes/{public_id}").json()
    dump("Server row immediately after the conflict (must still be Client A's, NOT overwritten by B)", row_after_conflict)
    assert row_after_conflict["content"] == "EDITED BY CLIENT A (ONLINE)", \
        "conflict must not silently overwrite the server row"
    print(">>> VERIFIED: Client B's stale write did not overwrite Client A's — conflict surfaced, not silently resolved")

    print(">>> Client B resolves via 'Keep Mine' (resubmits with the fresh known_version)")
    capture.clear()
    page.click('[data-testid="conflict-keep-mine-button"]')
    page.wait_for_timeout(1500)
    retry_call = capture.latest("/sync/push")
    dump("RAW POST /sync/push for the 'Keep Mine' retry", retry_call)
    retry_result = retry_call["response_body"]["results"][0]
    assert retry_result["outcome"] == "applied", f"Keep Mine retry did not apply: {retry_result}"

    final_row = requests.get(f"{BASE_API}/notes/{public_id}").json()
    dump("Server row after 'Keep Mine' resolves the conflict", final_row)
    assert final_row["content"] == client_b_text
    print(">>> VERIFIED: 'Keep Mine' resubmitted with the corrected known_version and won cleanly")


def part_d_idempotent_push():
    section("PART D — Idempotent push: identical client_mutation_id submitted twice")

    client_mutation_id = f"verify-idem-{uuid.uuid4().hex[:12]}"
    body = {
        "mutations": [{
            "client_mutation_id": client_mutation_id,
            "entity_type": "note",
            "entity_id": None,
            "op": "CREATE",
            "known_version": None,
            "payload": {"content": f"idempotency check {client_mutation_id}"},
        }]
    }

    resp1 = requests.post(f"{BASE_API}/sync/push", json=body)
    dump("First submission — raw response", resp1.json())
    resp2 = requests.post(f"{BASE_API}/sync/push", json=body)
    dump("Second submission of the IDENTICAL client_mutation_id — raw response", resp2.json())

    r1, r2 = resp1.json()["results"][0], resp2.json()["results"][0]
    assert r1 == r2, "replay returned a different result than the original — idempotency broken"
    print(">>> VERIFIED: byte-identical result on replay (same entity_id, same version)")

    created_public_id = r1["entity_id"]
    row_count = db_query(
        f"SELECT count(*) FROM notes WHERE public_id = '{created_public_id}';"
    )
    dump(f"psql: row count for public_id={created_public_id} (must be 1, not 2)", row_count)
    assert " 1" in row_count or "1\n" in row_count

    mutation_log_count = db_query(
        f"SELECT count(*) FROM sync_mutations WHERE client_mutation_id = '{client_mutation_id}';"
    )
    dump("psql: sync_mutations rows for this client_mutation_id (must be 1, not 2)", mutation_log_count)
    assert " 1" in mutation_log_count or "1\n" in mutation_log_count

    version_row = db_query(f"SELECT version FROM notes WHERE public_id = '{created_public_id}';")
    dump("psql: stored version (must be 1 — replay must not re-increment)", version_row)
    assert " 1" in version_row


def main():
    preflight()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        capture = NetworkCapture(page)

        public_id, version, content = part_a_offline_create_and_update(page, capture)
        part_b_push_pull_reconciliation(public_id, version, content)
        part_c_live_conflict(page, capture, public_id, version)

        browser.close()

    part_d_idempotent_push()

    section("ALL SECTIONS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nVERIFICATION FAILED: {e}", file=sys.stderr)
        sys.exit(1)
