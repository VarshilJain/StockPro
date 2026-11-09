"""
tests/test_single_active_session.py
Automated tests for single active session per user in StockPro Analytics Dashboard.

Covers:
1. Single active session enforcement across multiple logins (new login invalidates old).
2. HTTP 401 Unauthorized rejection on revoked/superseded tokens.
3. Logout invalidates active session in database.
4. Token refresh respects session validity (active succeeds, superseded fails).
5. Simultaneous/concurrent logins handled safely via atomic database locks.
6. Independent users do not interfere with each other's sessions.
7. Admin session revocation invalidates active session.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import app
from auth import create_access_token, generate_csrf_token, hash_password
from database import get_connection
from rate_limiter import login_rate_limiter
import admin_service


client = TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def reset_rate_limiter_fixture():
    login_rate_limiter.reset_ip("testclient")
    orig_max_ip = login_rate_limiter.max_ip_attempts
    orig_max_acc = login_rate_limiter.max_account_attempts
    login_rate_limiter.max_ip_attempts = 100
    login_rate_limiter.max_account_attempts = 100
    yield
    login_rate_limiter.reset_ip("testclient")
    login_rate_limiter.max_ip_attempts = orig_max_ip
    login_rate_limiter.max_account_attempts = orig_max_acc


def setup_test_user(email: str, password: str = "TestPassword123!", role: str = "user"):
    """Helper to ensure a clean test user exists in MySQL."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("DELETE FROM users WHERE email = %s", (email.lower(),))
    conn.commit()

    hashed = hash_password(password)
    cur.execute(
        """
        INSERT INTO users (email, hashed_password, name, role, is_active, approval_status, auth_provider, token_version, expires_at)
        VALUES (%s, %s, %s, %s, 1, 'approved', 'local', 1, DATE_ADD(NOW(), INTERVAL 365 DAY))
        """,
        (email.lower(), hashed, "Test Account", role),
    )
    user_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return user_id


def cleanup_test_user(email: str):
    """Helper to remove test user."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE email = %s", (email.lower(),))
    conn.commit()
    cur.close()
    conn.close()


def test_single_active_session_multiple_logins():
    """
    1. User logs in from Device 1 -> session is active (200).
    2. User logs in from Device 2 -> session 2 is active (200).
    3. Device 1 request is immediately rejected with 401 Unauthorized and session terminated detail.
    """
    email = "single_session_test1@stockpro.com"
    pwd = "TestPassword123!"
    user_id = setup_test_user(email, pwd)

    try:
        # Device 1 login
        c1 = TestClient(app, follow_redirects=False)
        r1 = c1.post("/api/auth/login", json={"email": email, "password": pwd})
        assert r1.status_code == 200, f"Device 1 login failed: {r1.text}"
        assert "access_token" in r1.cookies

        # Device 1 accesses protected endpoint
        me1 = c1.get("/api/auth/me")
        assert me1.status_code == 200
        assert me1.json()["email"] == email

        # Device 2 login for same user account
        c2 = TestClient(app, follow_redirects=False)
        r2 = c2.post("/api/auth/login", json={"email": email, "password": pwd})
        assert r2.status_code == 200, f"Device 2 login failed: {r2.text}"
        assert "access_token" in r2.cookies

        # Device 2 accesses protected endpoint -> succeeds
        me2 = c2.get("/api/auth/me")
        assert me2.status_code == 200
        assert me2.json()["email"] == email

        # Device 1 accesses protected endpoint -> REJECTED with 401
        me1_again = c1.get("/api/auth/me")
        assert me1_again.status_code == 401
        assert "signed in on another device" in me1_again.json()["detail"]
        assert me1_again.headers.get("X-Session-Terminated") == "1"

    finally:
        cleanup_test_user(email)


def test_revoked_token_cannot_access_protected_endpoints():
    """
    Protected endpoints reject superseded tokens with HTTP 401.
    """
    email = "revoked_token_test@stockpro.com"
    pwd = "TestPassword123!"
    user_id = setup_test_user(email, pwd)

    try:
        c1 = TestClient(app, follow_redirects=False)
        c1.post("/api/auth/login", json={"email": email, "password": pwd})

        c2 = TestClient(app, follow_redirects=False)
        c2.post("/api/auth/login", json={"email": email, "password": pwd})

        # Test various protected endpoints from the invalidated Device 1 session
        endpoints = [
            "/api/auth/me",
            "/api/watchlist",
            "/api/alerts",
            "/api/scans/saved",
        ]
        for ep in endpoints:
            res = c1.get(ep)
            assert res.status_code == 401, f"Expected 401 on {ep}, got {res.status_code}"
            data = res.json()
            assert "signed in on another device" in data.get("detail", "")
            assert res.headers.get("X-Session-Terminated") == "1"

    finally:
        cleanup_test_user(email)


def test_logout_invalidates_session_in_database():
    """
    Logout explicitly clears active_session_id in DB, invalidating subsequent requests.
    """
    email = "logout_test@stockpro.com"
    pwd = "TestPassword123!"
    user_id = setup_test_user(email, pwd)

    try:
        c = TestClient(app, follow_redirects=False)
        login_res = c.post("/api/auth/login", json={"email": email, "password": pwd})
        assert login_res.status_code == 200
        saved_token = c.cookies.get("access_token")

        # Verify active
        assert c.get("/api/auth/me").status_code == 200

        # Perform logout
        logout_res = c.post("/api/auth/logout")
        assert logout_res.status_code == 200

        # Verify DB active_session_id is NULL
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT active_session_id FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        assert row["active_session_id"] is None

        # Verify attempting to use the logged-out token returns 401
        c_hacker = TestClient(app, follow_redirects=False)
        c_hacker.cookies.set("access_token", saved_token)
        me_res = c_hacker.get("/api/auth/me")
        assert me_res.status_code == 401
        assert "signed in on another device" in me_res.json()["detail"]

    finally:
        cleanup_test_user(email)


def test_token_refresh_respects_session_validity():
    """
    /api/auth/refresh succeeds for active session, fails for superseded session.
    """
    email = "refresh_test@stockpro.com"
    pwd = "TestPassword123!"
    user_id = setup_test_user(email, pwd)

    try:
        # Device 1 login
        c1 = TestClient(app, follow_redirects=False)
        c1.post("/api/auth/login", json={"email": email, "password": pwd})

        # Device 1 refreshes token -> succeeds
        ref1 = c1.post("/api/auth/refresh")
        assert ref1.status_code == 200
        assert "access_token" in ref1.cookies

        # Device 2 logs in (supersedes Device 1)
        c2 = TestClient(app, follow_redirects=False)
        c2.post("/api/auth/login", json={"email": email, "password": pwd})

        # Device 1 attempts refresh -> REJECTED with 401
        ref1_superseded = c1.post("/api/auth/refresh")
        assert ref1_superseded.status_code == 401
        assert "signed in on another device" in ref1_superseded.json()["detail"]

        # Device 2 refreshes -> succeeds
        ref2 = c2.post("/api/auth/refresh")
        assert ref2.status_code == 200

    finally:
        cleanup_test_user(email)


def test_simultaneous_logins_atomic_handling():
    """
    Handle simultaneous login requests concurrently across workers/threads.
    Row-level locking guarantees safe serialization and exactly one active session.
    """
    email = "simultaneous_test@stockpro.com"
    pwd = "TestPassword123!"
    user_id = setup_test_user(email, pwd)

    try:
        num_logins = 6

        def do_login(idx: int):
            c = TestClient(app, follow_redirects=False)
            res = c.post("/api/auth/login", json={"email": email, "password": pwd})
            return (idx, res.status_code, c.cookies.get("access_token"))

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_logins) as executor:
            futures = [executor.submit(do_login, i) for i in range(num_logins)]
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())

        # All login calls should execute without database corruption or deadlock failure
        assert len(results) == num_logins
        for idx, status, token in results:
            assert status == 200, f"Simultaneous login {idx} failed with {status}"
            assert token is not None

        # Check DB active_session_id
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT active_session_id, token_version FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        cur.close()
        conn.close()

        active_sid = user_row["active_session_id"]
        assert active_sid is not None

        # Test all tokens against /api/auth/me
        active_count = 0
        superseded_count = 0

        for idx, status, token in results:
            t_client = TestClient(app, follow_redirects=False)
            t_client.cookies.set("access_token", token)
            check = t_client.get("/api/auth/me")
            if check.status_code == 200:
                active_count += 1
            elif check.status_code == 401:
                superseded_count += 1
                assert "signed in on another device" in check.json()["detail"]

        # EXACTLY ONE session must remain active!
        assert active_count == 1, f"Expected exactly 1 active session, got {active_count}"
        assert superseded_count == num_logins - 1

    finally:
        cleanup_test_user(email)


def test_independent_users_sessions_do_not_interfere():
    """
    Multiple distinct user accounts can be logged in simultaneously without invalidating each other.
    """
    email_a = "user_alpha@stockpro.com"
    email_b = "user_beta@stockpro.com"
    pwd = "TestPassword123!"

    user_a_id = setup_test_user(email_a, pwd)
    user_b_id = setup_test_user(email_b, pwd)

    try:
        cA1 = TestClient(app, follow_redirects=False)
        cA1.post("/api/auth/login", json={"email": email_a, "password": pwd})
        assert cA1.get("/api/auth/me").status_code == 200

        cB1 = TestClient(app, follow_redirects=False)
        cB1.post("/api/auth/login", json={"email": email_b, "password": pwd})
        assert cB1.get("/api/auth/me").status_code == 200

        # User A's session is still active
        assert cA1.get("/api/auth/me").status_code == 200

        # User A logs in on Device A2 -> A1 invalidated, A2 active
        cA2 = TestClient(app, follow_redirects=False)
        cA2.post("/api/auth/login", json={"email": email_a, "password": pwd})
        assert cA2.get("/api/auth/me").status_code == 200
        assert cA1.get("/api/auth/me").status_code == 401

        # User B's session B1 is STILL active and untouched!
        assert cB1.get("/api/auth/me").status_code == 200

    finally:
        cleanup_test_user(email_a)
        cleanup_test_user(email_b)


def test_admin_session_revocation():
    """
    Admin revoking user sessions invalidates active session immediately.
    """
    email = "admin_revoke_test@stockpro.com"
    admin_email = "admin_acting@stockpro.com"
    pwd = "TestPassword123!"

    user_id = setup_test_user(email, pwd, role="user")
    admin_id = setup_test_user(admin_email, pwd, role="admin")

    try:
        user_client = TestClient(app, follow_redirects=False)
        user_client.post("/api/auth/login", json={"email": email, "password": pwd})
        assert user_client.get("/api/auth/me").status_code == 200

        # Admin revokes user sessions
        admin_acting = {"id": admin_id, "email": admin_email, "role": "admin"}
        res = admin_service.revoke_user_sessions(admin_acting, user_id)
        assert "revoked" in res["message"]

        # User session is now immediately rejected with 401
        check = user_client.get("/api/auth/me")
        assert check.status_code == 401
        assert "signed in on another device" in check.json()["detail"]

    finally:
        cleanup_test_user(email)
        cleanup_test_user(admin_email)
