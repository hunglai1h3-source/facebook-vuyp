"""Functional regression for bulk Groups, assignments and campaign snapshots."""
import json
from io import BytesIO
import tempfile

from phase6_worker_command_regression import load_app, pair, heartbeat


def register(client, name):
    response = client.post("/register", data={
        "display_name": f"Phase 7 {name}", "username": f"phase7_{name}",
        "email": f"phase7_{name}@example.test", "password": "Phase7Test123!",
        "confirm_password": "Phase7Test123!",
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session["user_id"]


def allow_two_accounts(module, user_id):
    users = module.load_users()
    users[user_id]["max_facebook_accounts"] = 2
    users[user_id]["max_groups"] = 500
    module.save_users(users)


def run_checks(temp):
    module = load_app(temp)
    alice = module.app.test_client()
    alice_id = register(alice, "alice")
    allow_two_accounts(module, alice_id)

    urls = [f"https://www.facebook.com/groups/phase7-{index:03d}" for index in range(100)]
    payload = "\n".join(urls + [
        urls[0], "https://m.facebook.com/groups/phase7-000?ref=share",
        "https://example.com/not-facebook",
    ])
    response = alice.post("/groups/import", data={"group_urls": payload})
    assert response.status_code == 302
    assert module.load_groups(alice_id) == urls
    with alice.session_transaction() as session:
        summary = session["group_import_result"]
    assert summary == {"added": 100, "duplicates": 2, "invalid": 1,
                       "invalid_samples": ["https://example.com/not-facebook"]}
    print("PASS 1-2: imported 100 Groups; duplicate and invalid URL created no duplicate record")

    # Re-import the application with the same DATA_ROOT to emulate a restart.
    module = load_app(temp)
    assert module.load_groups(alice_id) == urls
    print("PASS 3: application restart preserves all imported Group data")

    alice = module.app.test_client()
    login = alice.post("/login", data={"login": "phase7_alice", "password": "Phase7Test123!"})
    assert login.status_code == 302
    allow_two_accounts(module, alice_id)
    assert alice.post("/groups/accounts", data={"display_name": "Account A", "facebook_user_id": "10001"}).status_code == 302
    assert alice.post("/groups/accounts", data={"display_name": "Account B", "facebook_user_id": "10002"}).status_code == 302
    account_a, account_b = module.load_facebook_accounts(alice_id)

    even = module.evenly_assign_groups(urls, [account_a["account_id"], account_b["account_id"]])
    response = alice.post("/groups/assign", data={"assignments": json.dumps(even)})
    assert response.status_code == 302
    mapping = module.load_group_assignments(alice_id)
    assert sum(value == account_a["account_id"] for value in mapping.values()) == 50
    assert sum(value == account_b["account_id"] for value in mapping.values()) == 50
    print("PASS 4: evenly assigned 100 Groups across two Facebook accounts (50/50)")

    changed = [{"group_url": urls[0], "account_id": account_b["account_id"]}]
    assert alice.post("/groups/assign", data={"assignments": json.dumps(changed)}).status_code == 302
    mapping = module.load_group_assignments(alice_id)
    assert mapping[urls[0]] == account_b["account_id"]
    assert sum(value == account_b["account_id"] for value in mapping.values()) == 51
    before_conflict = dict(mapping)
    conflict = [
        {"group_url": urls[1], "account_id": account_a["account_id"]},
        {"group_url": urls[1], "account_id": account_b["account_id"]},
    ]
    alice.post("/groups/assign", data={"assignments": json.dumps(conflict)})
    assert module.load_group_assignments(alice_id) == before_conflict
    print("PASS 5: manual reassignment saves; conflicting duplicate assignment is rejected atomically")

    alice.post("/save-post", data={
        "campaign_name": "Snapshot test", "content": "Phase 7 content",
        "min_delay": "0", "max_delay": "0",
    })
    pair_a, agent_a = pair(alice, "phase7-a")
    pair_b, agent_b = pair(alice, "phase7-b")
    assert heartbeat(alice, agent_a).status_code == 200
    assert heartbeat(alice, agent_b).status_code == 200
    module.bind_facebook_account_device(alice_id, account_a["account_id"], pair_a["device_id"])
    module.bind_facebook_account_device(alice_id, account_b["account_id"], pair_b["device_id"])
    assert alice.post("/run-campaign").status_code == 302
    engine_campaign = module.load_engine_campaigns(alice_id)[0]
    snapshot = json.loads(json.dumps(engine_campaign["account_group_snapshot"]))
    flattened = [url for bucket in snapshot for url in bucket["groups"]]
    assert sorted(flattened) == sorted(urls) and len(flattened) == len(set(flattened)) == 100
    assert {bucket["account_name"] for bucket in snapshot} == {"Account A", "Account B"}
    module.save_group_assignments(alice_id, [{"group_url": urls[2], "account_id": account_b["account_id"]}])
    assert module.load_engine_campaigns(alice_id)[0]["account_group_snapshot"] == snapshot
    print("PASS 6: campaign stores an immutable account -> Groups snapshot")

    csv_url = "https://www.facebook.com/groups/phase7-csv"
    csv_import = alice.post("/groups/import", data={
        "group_file": (BytesIO(("group_url\n" + csv_url + "\n").encode()), "groups.csv")
    }, content_type="multipart/form-data")
    assert csv_import.status_code == 302 and csv_url in module.load_groups(alice_id)
    print("PASS: CSV/TXT-compatible upload parser imports valid Group URLs")

    bob = module.app.test_client()
    bob_id = register(bob, "bob")
    allow_two_accounts(module, bob_id)
    bob_account = module.create_facebook_account(bob_id, "Bob Account")
    cross_group = bob.post("/groups/assign", data={"assignments": json.dumps([
        {"group_url": urls[0], "account_id": bob_account["account_id"]}
    ])})
    assert cross_group.status_code == 302 and module.load_group_assignments(bob_id) == {}
    try:
        module.save_group_assignments(alice_id, [
            {"group_url": urls[3], "account_id": bob_account["account_id"]}
        ])
        raise AssertionError("cross-tenant account assignment was accepted")
    except ValueError:
        pass
    bob_page = bob.get("/groups").get_data(as_text=True)
    assert urls[0] not in bob_page, "Alice Group URL leaked into Bob page"
    assert ">Account A<" not in bob_page, "Alice account option leaked into Bob page"
    assert bob.post("/groups/bulk-delete", data={"group_urls": urls[0]}).status_code == 302
    assert len(module.load_groups(alice_id)) == 101
    print("PASS 7: tenant B cannot view, assign or delete tenant A Groups/accounts")

    # A second restart validates accounts and mappings, not just raw URLs.
    module = load_app(temp)
    assert len(module.load_facebook_accounts(alice_id)) == 2
    assert len(module.load_group_assignments(alice_id)) == 100
    assert module.load_engine_campaigns(alice_id)[0]["account_group_snapshot"] == snapshot
    print("PASS: account and mapping persistence survives restart")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fbpp-phase7-") as temp:
        run_checks(temp)
