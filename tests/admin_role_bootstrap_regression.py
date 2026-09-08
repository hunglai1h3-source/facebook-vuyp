"""Functional regression for role-based admin bootstrap and promotion."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from phase6_worker_command_regression import load_app
from phase7_bulk_groups_regression import register


def run_checks():
    with tempfile.TemporaryDirectory(prefix="fbpp-admin-boot-") as temp:
        # 1. Start with no preconfigured admin
        module = load_app(temp)
        client_regular = module.app.test_client()
        user_regular = register(client_regular, "regular_test")

        # Anonymous checks
        anon = module.app.test_client()
        assert anon.get("/admin").status_code == 302
        assert anon.get("/api/admin/dashboard").status_code == 401
        assert anon.get("/api/admin/dashboard").get_json()["error"] == "Admin access required."

        # Regular user checks
        assert client_regular.get("/admin").status_code == 403
        assert client_regular.get("/api/admin/dashboard").status_code == 403
        assert client_regular.get("/api/admin/dashboard").get_json()["error"] == "Admin access required."
        print("PASS 1: anonymous redirected/401, regular user strictly forbidden (403)")

        # 2. Promote user_regular to admin via promote_user_to_admin()
        success, promoted = module.promote_user_to_admin("phase7_regular_test")
        assert success is True
        assert promoted["role"] == "admin"
        assert module.find_user_by_id(user_regular)["role"] == "admin"

        # Now client_regular has admin access
        admin_page = client_regular.get("/admin")
        assert admin_page.status_code == 200
        assert "Operations overview" in admin_page.get_data(as_text=True)
        api_res = client_regular.get("/api/admin/dashboard")
        assert api_res.status_code == 200
        assert api_res.get_json()["ok"] is True
        print("PASS 2: promoted user accesses /admin (200) and /api/admin/dashboard (200)")

        # 3. Create a second user and promote via in-app API
        client_user2 = module.app.test_client()
        user2_id = register(client_user2, "second_user")
        assert client_user2.get("/admin").status_code == 403

        # client_regular promotes second_user
        promo_res = client_regular.post(f"/api/admin/users/{user2_id}/role", json={"role": "admin"})
        assert promo_res.status_code == 200
        assert promo_res.get_json()["role"] == "admin"
        assert module.find_user_by_id(user2_id)["role"] == "admin"

        # second_user now has admin access
        assert client_user2.get("/admin").status_code == 200

        # Self-demotion guard
        self_demote = client_regular.post(f"/api/admin/users/{user_regular}/role", json={"role": "user"})
        assert self_demote.status_code == 409
        assert "không thể tự hạ quyền" in self_demote.get_json()["error"]
        print("PASS 3: in-app /api/admin/users/<id>/role promotes user, self-demotion blocked (409)")

        # 4. Bootstrap via ADMIN_USERNAMES environment variable
        module.ADMIN_USERNAMES.add("phase7_env_admin")
        client_env = module.app.test_client()
        env_admin_id = register(client_env, "env_admin")
        assert module.find_user_by_id(env_admin_id)["role"] == "admin"
        assert client_env.get("/admin").status_code == 200
        print("PASS 4: user matching ADMIN_USERNAMES gets role=admin automatically upon registration")

        # 5. CLI promote_admin.py script
        cli_check = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "promote_admin.py"), "--check", "phase7_env_admin"],
            cwd=ROOT, env=dict(os.environ, DATA_ROOT=temp), capture_output=True, text=True
        )
        assert cli_check.returncode == 0
        assert "Role: admin" in cli_check.stdout

        # CLI demote and verify
        cli_demote = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "promote_admin.py"), "--demote", "phase7_env_admin"],
            cwd=ROOT, env=dict(os.environ, DATA_ROOT=temp), capture_output=True, text=True
        )
        assert cli_demote.returncode == 0
        assert "role updated to 'user'" in cli_demote.stdout

        # CLI promote and verify
        cli_promote = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "promote_admin.py"), "phase7_env_admin"],
            cwd=ROOT, env=dict(os.environ, DATA_ROOT=temp), capture_output=True, text=True
        )
        assert cli_promote.returncode == 0
        assert "role updated to 'admin'" in cli_promote.stdout
        print("PASS 5: CLI script promote_admin.py supports check, demote, and promote")


if __name__ == "__main__":
    run_checks()
    print("ALL ADMIN BOOTSTRAP & PROMOTION CHECKS PASSED!")
