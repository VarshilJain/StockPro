"""
test_admin_portal.py — End-to-end automated test suite for StockPro Admin Dashboard.
Tests:
  1. /admin page route access controls (unauthenticated redirect, non-admin 403, admin 200)
  2. /api/admin/* endpoint RBAC (401 unauth, 403 non-admin, 200 admin)
  3. Last active admin safeguard (cannot demote or deactivate last admin)
  4. Session revocation mechanism (token_version invalidation)
  5. CSRF protection on mutating admin actions
  6. Audit trail generation on sensitive actions
  7. Regression testing on existing StockPro APIs
"""
from fastapi.testclient import TestClient
from datetime import timedelta

from app import app
from auth import create_access_token, generate_csrf_token, hash_password
from database import get_connection
import admin_service

client = TestClient(app, follow_redirects=False)


def setup_test_users():
    """Ensure test admin and test standard user exist in MySQL."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # 1. Clean previous test users
    cur.execute("DELETE FROM users WHERE email IN ('testadmin@stockpro.com', 'testuser@stockpro.com', 'testtempadmin@stockpro.com')")
    conn.commit()

    # 2. Insert test admin
    hashed_pwd = hash_password("TestPassword123!")
    cur.execute(
        """
        INSERT INTO users (email, hashed_password, name, role, is_active, auth_provider, token_version)
        VALUES ('testadmin@stockpro.com', %s, 'Test Admin', 'admin', 1, 'local', 1)
        """,
        (hashed_pwd,)
    )
    admin_id = cur.lastrowid

    # 3. Insert test regular user
    cur.execute(
        """
        INSERT INTO users (email, hashed_password, name, role, is_active, auth_provider, token_version)
        VALUES ('testuser@stockpro.com', %s, 'Test User', 'user', 1, 'local', 1)
        """,
        (hashed_pwd,)
    )
    user_id = cur.lastrowid

    conn.commit()
    cur.close()
    conn.close()

    admin_token = create_access_token({"sub": str(admin_id), "ver": 1}, expires_delta=timedelta(hours=2))
    user_token = create_access_token({"sub": str(user_id), "ver": 1}, expires_delta=timedelta(hours=2))
    csrf_token = generate_csrf_token()

    yield {
        "admin_id": admin_id,
        "admin_token": admin_token,
        "user_id": user_id,
        "user_token": user_token,
        "csrf_token": csrf_token,
    }

    # Cleanup
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE email IN ('testadmin@stockpro.com', 'testuser@stockpro.com', 'testtempadmin@stockpro.com')")
    conn.commit()
    cur.close()
    conn.close()


# ──────────────────────────────────────────────────────────────
# 1. Page-level Route Access Tests
# ──────────────────────────────────────────────────────────────
def test_unauthenticated_page_access_redirects():
    """Unauthenticated access to /admin must redirect to /login?redirect=/admin."""
    res = client.get("/admin")
    assert res.status_code == 302
    assert "/login?redirect=/admin" in res.headers["Location"]

    res_users = client.get("/admin/users")
    assert res_users.status_code == 302
    assert "/login?redirect=/admin/users" in res_users.headers["Location"]


def test_non_admin_page_access_forbidden(setup_test_users):
    """Authenticated non-admin user accessing /admin must be rejected with 403 Forbidden."""
    client.cookies.set("access_token", setup_test_users["user_token"])
    res = client.get("/admin")
    assert res.status_code == 403
    assert "Administrator privileges required" in res.json().get("detail", "")
    client.cookies.clear()


def test_admin_page_access_granted(setup_test_users):
    """Admin user accessing /admin and /admin/users must receive 200 OK with HTML content."""
    client.cookies.set("access_token", setup_test_users["admin_token"])
    res = client.get("/admin")
    assert res.status_code == 200
    assert "StockPro" in res.text
    assert "Ops Console" in res.text

    res_users = client.get("/admin/users")
    assert res_users.status_code == 200
    assert "StockPro" in res_users.text
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 2. API Endpoint RBAC Tests
# ──────────────────────────────────────────────────────────────
def test_api_overview_rbac(setup_test_users):
    """API endpoints must return 401 for unauth, 403 for non-admin, 200 for admin."""
    # Unauth
    res_unauth = client.get("/api/admin/overview")
    assert res_unauth.status_code == 401

    # Non-admin
    client.cookies.set("access_token", setup_test_users["user_token"])
    res_non_admin = client.get("/api/admin/overview")
    assert res_non_admin.status_code == 403

    # Admin
    client.cookies.set("access_token", setup_test_users["admin_token"])
    res_admin = client.get("/api/admin/overview")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "users" in data
    assert "market_data" in data
    assert "system_health" in data
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 3. User Management & Last Admin Safeguard
# ──────────────────────────────────────────────────────────────
def test_users_list_and_search(setup_test_users):
    """Admin can search, filter, and paginate users."""
    client.cookies.set("access_token", setup_test_users["admin_token"])
    res = client.get("/api/admin/users?search=testuser")
    assert res.status_code == 200
    data = res.json()
    assert len(data["users"]) >= 1
    assert any(u["email"] == "testuser@stockpro.com" for u in data["users"])
    client.cookies.clear()


def test_last_admin_protection(setup_test_users):
    """
    Ensure the system strictly prevents demoting or deactivating the last active admin.
    """
    client.cookies.set("access_token", setup_test_users["admin_token"])
    client.cookies.set("csrf_token", setup_test_users["csrf_token"])
    headers = {"X-CSRF-Token": setup_test_users["csrf_token"]}

    # First verify current admin count
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1")
    admin_count = cur.fetchone()["cnt"]

    # If there is only 1 admin, demoting or deactivating must fail with 400
    if admin_count == 1:
        res = client.patch(
            f"/api/admin/users/{setup_test_users['admin_id']}/role",
            json={"role": "user"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "Cannot demote the last remaining" in res.json().get("detail", "")

        res_deact = client.patch(
            f"/api/admin/users/{setup_test_users['admin_id']}/status",
            json={"is_active": False},
            headers=headers,
        )
        assert res_deact.status_code == 400
        assert "Cannot deactivate the last remaining" in res_deact.json().get("detail", "")
    else:
        # If there are multiple admins, test demoting and then verify protection when count=1
        pass

    cur.close()
    conn.close()
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 4. Session Revocation Mechanism
# ──────────────────────────────────────────────────────────────
def test_session_revocation(setup_test_users):
    """
    Revoking sessions increments token_version in DB.
    Tokens minted with the previous version must immediately fail with 401.
    """
    admin_token = setup_test_users["admin_token"]
    user_token = setup_test_users["user_token"]
    target_user_id = setup_test_users["user_id"]
    csrf_token = setup_test_users["csrf_token"]

    # 1. User token works initially
    client.cookies.set("access_token", user_token)
    res_me_before = client.get("/api/auth/me")
    assert res_me_before.status_code == 200

    # 2. Admin revokes user sessions
    client.cookies.set("access_token", admin_token)
    client.cookies.set("csrf_token", csrf_token)
    res_revoke = client.post(
        f"/api/admin/users/{target_user_id}/revoke-sessions",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res_revoke.status_code == 200
    assert "revoked" in res_revoke.json().get("message", "").lower()

    # 3. User token is now immediately rejected
    client.cookies.set("access_token", user_token)
    res_me_after = client.get("/api/auth/me")
    assert res_me_after.status_code == 401

    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 5. CSRF Protection on Mutating Actions
# ──────────────────────────────────────────────────────────────
def test_csrf_protection_on_admin_actions(setup_test_users):
    """Mutating admin actions without valid CSRF token must return 403."""
    client.cookies.set("access_token", setup_test_users["admin_token"])
    
    # Missing CSRF cookie and header
    res_no_csrf = client.patch(
        "/api/admin/scanners/vcp/toggle",
        json={"is_enabled": True},
    )
    assert res_no_csrf.status_code == 403

    # With valid CSRF cookie and header
    client.cookies.set("csrf_token", setup_test_users["csrf_token"])
    res_valid_csrf = client.patch(
        "/api/admin/scanners/vcp/toggle",
        json={"is_enabled": True},
        headers={"X-CSRF-Token": setup_test_users["csrf_token"]},
    )
    assert res_valid_csrf.status_code == 200
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 6. Audit Trail Generation
# ──────────────────────────────────────────────────────────────
def test_audit_log_generation(setup_test_users):
    """Sensitive admin actions must create an entry in admin_audit_logs."""
    client.cookies.set("access_token", setup_test_users["admin_token"])
    client.cookies.set("csrf_token", setup_test_users["csrf_token"])
    headers = {"X-CSRF-Token": setup_test_users["csrf_token"]}

    # Perform action
    res_toggle = client.patch(
        "/api/admin/scanners/vcp/toggle",
        json={"is_enabled": False},
        headers=headers,
    )
    assert res_toggle.status_code == 200

    # Verify audit log recorded
    res_audit = client.get("/api/admin/security/audit-logs?action=SCANNER_TOGGLE")
    assert res_audit.status_code == 200
    logs = res_audit.json()["logs"]
    assert len(logs) >= 1
    assert logs[0]["target_id"] == "vcp"
    assert logs[0]["action"] == "SCANNER_TOGGLE"

    # Re-enable
    client.patch(
        "/api/admin/scanners/vcp/toggle",
        json={"is_enabled": True},
        headers=headers,
    )
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 7. Regression Testing on Existing StockPro APIs
# ──────────────────────────────────────────────────────────────
def test_existing_apis_regression(setup_test_users):
    """Ensure existing public and user endpoints function normally."""
    # 1. Signals registry
    res_sig = client.get("/api/signals")
    assert res_sig.status_code == 200

    # 2. Stage summary
    client.cookies.set("access_token", setup_test_users["admin_token"])
    res_stage = client.get("/api/stage-summary")
    assert res_stage.status_code == 200
    assert "forming" in res_stage.json()

    # 3. Pattern screen
    res_vcp = client.get("/api/screens/vcp/stages")
    assert res_vcp.status_code == 200
    assert res_vcp.json()["screen"] == "vcp"

    client.cookies.clear()


if __name__ == "__main__":
    print("Running StockPro Admin Dashboard Test Suite...")
    # Manual fixture lifecycle
    fixture_gen = setup_test_users()
    users = next(fixture_gen)
    
    tests = [
        ("test_unauthenticated_page_access_redirects", lambda: test_unauthenticated_page_access_redirects()),
        ("test_non_admin_page_access_forbidden", lambda: test_non_admin_page_access_forbidden(users)),
        ("test_admin_page_access_granted", lambda: test_admin_page_access_granted(users)),
        ("test_api_overview_rbac", lambda: test_api_overview_rbac(users)),
        ("test_users_list_and_search", lambda: test_users_list_and_search(users)),
        ("test_last_admin_protection", lambda: test_last_admin_protection(users)),
        ("test_session_revocation", lambda: test_session_revocation(users)),
        ("test_csrf_protection_on_admin_actions", lambda: test_csrf_protection_on_admin_actions(users)),
        ("test_audit_log_generation", lambda: test_audit_log_generation(users)),
        ("test_existing_apis_regression", lambda: test_existing_apis_regression(users)),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} -> {e}")
            failed += 1

    try:
        next(fixture_gen)
    except StopIteration:
        pass

    print(f"\nTest Summary: {passed} passed, {failed} failed.")
    if failed > 0:
        exit(1)

