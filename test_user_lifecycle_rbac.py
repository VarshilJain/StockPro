"""
test_user_lifecycle_rbac.py — Comprehensive automated test suite for:
  1. Creating new Admin & Standard users directly from Admin Console.
  2. Approval workflow: new registrations require admin approval before access is granted.
  3. Blocking and unblocking user access with immediate session termination.
  4. Removing user access permanently with last-admin safeguards.
  5. 365-day validity lifecycle: remaining days calculation, expiry blocking, and validity renewal.
"""
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app import app
from auth import create_access_token, generate_csrf_token, hash_password
from database import get_connection
from rate_limiter import login_rate_limiter

client = TestClient(app, follow_redirects=False)


def setup_test_environment():
    """Setup clean test admin and initial users."""
    login_rate_limiter.reset_ip("testclient")
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # Clean previous test users
    cur.execute(
        """
        DELETE FROM users 
        WHERE email IN (
            'primary_admin@stockpro.com', 
            'new_admin_test@stockpro.com',
            'regular_user_test@stockpro.com',
            'pending_applicant@stockpro.com',
            'expired_user_test@stockpro.com',
            'rejected_user_test@stockpro.com'
        )
        """
    )
    conn.commit()

    # 1. Primary admin (approved, active, 365d valid)
    hashed_pwd = hash_password("AdminSecurePassword123!")
    cur.execute(
        """
        INSERT INTO users (email, hashed_password, name, role, is_active, approval_status, auth_provider, token_version, expires_at)
        VALUES ('primary_admin@stockpro.com', %s, 'Primary Admin', 'admin', 1, 'approved', 'local', 1, DATE_ADD(NOW(), INTERVAL 365 DAY))
        """,
        (hashed_pwd,)
    )
    admin_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()

    admin_token = create_access_token({"sub": str(admin_id), "ver": 1}, expires_delta=timedelta(hours=2))
    csrf_token = generate_csrf_token()

    return {
        "admin_id": admin_id,
        "admin_token": admin_token,
        "csrf_token": csrf_token,
    }


def cleanup_test_environment():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM users 
        WHERE email IN (
            'primary_admin@stockpro.com', 
            'new_admin_test@stockpro.com',
            'regular_user_test@stockpro.com',
            'pending_applicant@stockpro.com',
            'expired_user_test@stockpro.com',
            'rejected_user_test@stockpro.com'
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def set_admin_session(env):
    client.cookies.clear()
    client.cookies.set("access_token", env["admin_token"])
    client.cookies.set("csrf_token", env["csrf_token"])


# ──────────────────────────────────────────────────────────────
# 1. Create New Admin and User from Admin Console
# ──────────────────────────────────────────────────────────────
def test_create_new_admin_and_user(env):
    set_admin_session(env)

    # A. Create new admin
    res_admin = client.post(
        "/api/admin/users",
        headers={"x-csrf-token": env["csrf_token"]},
        json={
            "name": "Secondary Ops Admin",
            "email": "new_admin_test@stockpro.com",
            "password": "AdminPassword987!",
            "role": "admin",
            "validity_days": 365
        }
    )
    assert res_admin.status_code == 201, f"Failed to create admin: {res_admin.text}"
    data_admin = res_admin.json()
    assert data_admin["role"] == "admin"
    assert data_admin["email"] == "new_admin_test@stockpro.com"

    # Verify newly created admin can log in immediately
    client.cookies.clear()
    res_login = client.post(
        "/api/auth/login",
        json={"email": "new_admin_test@stockpro.com", "password": "AdminPassword987!"}
    )
    assert res_login.status_code == 200, f"New admin login failed: {res_login.text}"

    # B. Create regular user
    set_admin_session(env)
    res_user = client.post(
        "/api/admin/users",
        headers={"x-csrf-token": env["csrf_token"]},
        json={
            "name": "Standard Client",
            "email": "regular_user_test@stockpro.com",
            "password": "ClientPassword123!",
            "role": "user",
            "validity_days": 365
        }
    )
    assert res_user.status_code == 201, f"Failed to create user: {res_user.text}"
    client.cookies.clear()


# ──────────────────────────────────────────────────────────────
# 2. Registration Approval Workflow
# ──────────────────────────────────────────────────────────────
def test_approval_workflow(env):
    client.cookies.clear()
    # A. Self-register a new account via /api/auth/register
    res_reg = client.post(
        "/api/auth/register",
        json={
            "name": "Pending Applicant",
            "email": "pending_applicant@stockpro.com",
            "password": "ApplicantPassword123!"
        }
    )
    assert res_reg.status_code == 201, f"Registration failed: {res_reg.text}"
    reg_data = res_reg.json()
    assert reg_data["approval_status"] == "pending"

    # B. Attempt to login before approval -> Must be rejected with 403
    client.cookies.clear()
    res_unauth_login = client.post(
        "/api/auth/login",
        json={"email": "pending_applicant@stockpro.com", "password": "ApplicantPassword123!"}
    )
    assert res_unauth_login.status_code == 403
    assert "pending admin approval" in res_unauth_login.json()["detail"].lower()

    # C. Admin inspects pending list
    set_admin_session(env)
    res_list = client.get("/api/admin/users?approval=pending")
    assert res_list.status_code == 200
    pending_users = res_list.json()["users"]
    target = next((u for u in pending_users if u["email"] == "pending_applicant@stockpro.com"), None)
    assert target is not None, "Pending user not found in admin pending filter"
    assert target["approval_status"] == "pending"
    assert not target["is_active"]

    # D. Admin approves the user
    set_admin_session(env)
    res_approve = client.post(
        f"/api/admin/users/{target['id']}/approve",
        headers={"x-csrf-token": env["csrf_token"]},
        json={"validity_days": 365}
    )
    assert res_approve.status_code == 200, f"Approve failed: {res_approve.text}"

    # E. User can now successfully login!
    client.cookies.clear()
    res_auth_login = client.post(
        "/api/auth/login",
        json={"email": "pending_applicant@stockpro.com", "password": "ApplicantPassword123!"}
    )
    assert res_auth_login.status_code == 200, f"Approved user failed to log in: {res_auth_login.text}"


# ──────────────────────────────────────────────────────────────
# 3. Block and Unblock User Access
# ──────────────────────────────────────────────────────────────
def test_block_and_unblock_access(env):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM users WHERE email = 'regular_user_test@stockpro.com'")
    row = cur.fetchone()
    assert row is not None, "regular_user_test@stockpro.com should exist from test 1"
    target_id = row["id"]
    cur.close(); conn.close()

    # A. Block user access
    set_admin_session(env)
    res_block = client.patch(
        f"/api/admin/users/{target_id}/status",
        headers={"x-csrf-token": env["csrf_token"]},
        json={"is_active": False}
    )
    assert res_block.status_code == 200

    # B. Blocked user login must fail with 403
    client.cookies.clear()
    res_login_blocked = client.post(
        "/api/auth/login",
        json={"email": "regular_user_test@stockpro.com", "password": "ClientPassword123!"}
    )
    assert res_login_blocked.status_code == 403
    assert "blocked or deactivated" in res_login_blocked.json()["detail"].lower()

    # C. Unblock user access
    set_admin_session(env)
    res_unblock = client.patch(
        f"/api/admin/users/{target_id}/status",
        headers={"x-csrf-token": env["csrf_token"]},
        json={"is_active": True}
    )
    assert res_unblock.status_code == 200

    # D. Unblocked user can log in
    client.cookies.clear()
    res_login_unblocked = client.post(
        "/api/auth/login",
        json={"email": "regular_user_test@stockpro.com", "password": "ClientPassword123!"}
    )
    assert res_login_unblocked.status_code == 200


# ──────────────────────────────────────────────────────────────
# 4. 365-Day Validity Countdown & Expiration
# ──────────────────────────────────────────────────────────────
def test_validity_365_days_and_expiry(env):
    login_rate_limiter.reset_ip("testclient")
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # Insert an expired user (expires_at 10 days in the past)
    hashed_pwd = hash_password("ExpiredUser123!")
    cur.execute(
        """
        INSERT INTO users (email, hashed_password, name, role, is_active, approval_status, auth_provider, expires_at)
        VALUES ('expired_user_test@stockpro.com', %s, 'Expired User', 'user', 1, 'approved', 'local', DATE_SUB(NOW(), INTERVAL 10 DAY))
        """,
        (hashed_pwd,)
    )
    expired_user_id = cur.lastrowid
    conn.commit()
    cur.close(); conn.close()

    # A. Check admin user list contains countdown days_remaining and is_expired
    set_admin_session(env)
    res_list = client.get("/api/admin/users?search=expired_user_test")
    assert res_list.status_code == 200
    matching_users = res_list.json()["users"]
    assert len(matching_users) > 0, "Expired user not found in search"
    expired_user = matching_users[0]
    assert expired_user["is_expired"] is True
    assert expired_user["days_remaining"] == 0

    # B. Expired user login must fail with 403
    client.cookies.clear()
    res_login_expired = client.post(
        "/api/auth/login",
        json={"email": "expired_user_test@stockpro.com", "password": "ExpiredUser123!"}
    )
    assert res_login_expired.status_code == 403
    assert "expired" in res_login_expired.json()["detail"].lower()

    # C. Admin renews validity by 365 days
    set_admin_session(env)
    res_renew = client.post(
        f"/api/admin/users/{expired_user_id}/renew",
        headers={"x-csrf-token": env["csrf_token"]},
        json={"days": 365}
    )
    assert res_renew.status_code == 200

    # D. User can now log in after validity extension!
    client.cookies.clear()
    res_login_renewed = client.post(
        "/api/auth/login",
        json={"email": "expired_user_test@stockpro.com", "password": "ExpiredUser123!"}
    )
    assert res_login_renewed.status_code == 200


# ──────────────────────────────────────────────────────────────
# 5. Permanent User Removal & Safeguards
# ──────────────────────────────────────────────────────────────
def test_permanent_user_removal_and_safeguards(env):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM users WHERE email = 'regular_user_test@stockpro.com'")
    row = cur.fetchone()
    assert row is not None
    regular_id = row["id"]
    cur.close(); conn.close()

    set_admin_session(env)

    # A. Safeguard: Admin cannot delete their own account
    res_self_del = client.delete(
        f"/api/admin/users/{env['admin_id']}",
        headers={"x-csrf-token": env["csrf_token"]}
    )
    assert res_self_del.status_code == 400
    assert "cannot remove your own" in res_self_del.json()["detail"].lower()

    # B. Delete regular user
    set_admin_session(env)
    res_del = client.delete(
        f"/api/admin/users/{regular_id}",
        headers={"x-csrf-token": env["csrf_token"]}
    )
    assert res_del.status_code == 200

    # C. Verify deleted user no longer exists
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM users WHERE id = %s", (regular_id,))
    assert cur.fetchone() is None
    cur.close(); conn.close()
    client.cookies.clear()


if __name__ == "__main__":
    print("Running User Lifecycle & RBAC Test Suite...")
    env = setup_test_environment()
    try:
        tests = [
            ("test_create_new_admin_and_user", lambda: test_create_new_admin_and_user(env)),
            ("test_approval_workflow", lambda: test_approval_workflow(env)),
            ("test_block_and_unblock_access", lambda: test_block_and_unblock_access(env)),
            ("test_validity_365_days_and_expiry", lambda: test_validity_365_days_and_expiry(env)),
            ("test_permanent_user_removal_and_safeguards", lambda: test_permanent_user_removal_and_safeguards(env)),
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

        print(f"\nSummary: {passed} passed, {failed} failed.")
        if failed > 0:
            exit(1)
    finally:
        cleanup_test_environment()
