"""
test_security.py — Comprehensive security test suite for StockPro.
Covers:
  1. JWT secret validation & environment gating
  2. Expired / forged JWT handling
  3. RBAC authorization (Admin vs User privileges on /api/data)
  4. CSRF protection (Header/Cookie double-submit validation)
  5. Password complexity validation (12+ characters, alphanumeric)
  6. Rate limiting (IP & Account sliding window, 429 response)
  7. API Resource Limits (date range > 5 years, batch > 5000, condition count > 20)
  8. Security Headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy)
  9. Error Handling & Information Leakage Prevention
"""
import os
import time
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
from jose import jwt
from fastapi.testclient import TestClient

# Ensure development environment during testing unless overridden
os.environ["STOCKPRO_ENV"] = "development"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-32-characters-long-123456"

from app import app
import config
from config import JWT_SECRET_KEY, JWT_ALGORITHM
from auth import create_access_token, generate_csrf_token, get_current_user, hash_password
from rate_limiter import LoginRateLimiter, InMemorySlidingWindowStore, get_client_ip



class SecurityHardeningTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.user_data = {
            "id": 42,
            "email": "user@example.com",
            "name": "Standard User",
            "role": "user",
            "is_active": True,
        }
        self.admin_data = {
            "id": 1,
            "email": "admin@example.com",
            "name": "Admin User",
            "role": "admin",
            "is_active": True,
        }
        app.dependency_overrides = {}

    def tearDown(self):
        app.dependency_overrides = {}

    # ──────────────────────────────────────────────────────────
    # 1. JWT & Production Config Hardening
    # ──────────────────────────────────────────────────────────
    def test_production_fails_without_strong_jwt_secret(self):
        """Startup check should reject empty or weak JWT secrets in production."""
        with self.assertRaises(RuntimeError):
            config.validate_security_config(
                is_prod=True,
                jwt_secret="short",
                db_config={"host": "h", "user": "u", "password": "p", "database": "d"}
            )

    def test_production_fails_with_default_root_db(self):
        """Startup check should reject root/root db credentials in production."""
        with self.assertRaises(RuntimeError):
            config.validate_security_config(
                is_prod=True,
                jwt_secret="a-very-strong-production-key-at-least-32-chars-long",
                db_config={"host": "localhost", "user": "root", "password": "root", "database": "stock_data"}
            )

    def test_expired_jwt_rejected(self):
        """Expired tokens must return 401 Unauthorized."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        token = jwt.encode(
            {"sub": "user@example.com", "id": 42, "exp": past},
            JWT_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 401)

    def test_tampered_jwt_rejected(self):
        """Tokens signed with an arbitrary different secret must return 401."""
        tampered_token = jwt.encode(
            {"sub": "user@example.com", "id": 42},
            "wrong-attacker-secret-key-32-chars-xxxx",
            algorithm=JWT_ALGORITHM,
        )
        res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered_token}"})
        self.assertEqual(res.status_code, 401)

    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_google_auth_invalid_token_returns_401(self, mock_verify):
        """Invalid Google token must return 401 Unauthorized."""
        mock_verify.side_effect = ValueError("Invalid token")
        res = self.client.post("/api/auth/google", json={"credential": "invalid-google-token"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("Invalid Google authentication token", res.json().get("detail", ""))

    @patch("routers.auth.get_connection")
    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_google_auth_success_sets_cookies_and_provisions_user(self, mock_verify, mock_db):
        """Valid Google token provisions a new user and sets access_token and csrf_token cookies."""
        mock_verify.return_value = {
            "email": "newgoogleuser@example.com",
            "name": "Google User",
            "sub": "google-oauth2-123456789",
            "email_verified": True,
        }
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # First SELECT returns None (new user), so it does INSERT
        mock_cursor.fetchone.return_value = None
        mock_cursor.lastrowid = 101
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = self.client.post("/api/auth/google", json={"credential": "valid-mocked-google-token"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", res.cookies)
        self.assertIn("csrf_token", res.cookies)
        self.assertEqual(res.json()["user"]["email"], "newgoogleuser@example.com")

    @patch("routers.auth.get_connection")
    def test_reset_password_invalid_current_pwd_returns_401(self, mock_db):
        """Reset password with wrong current password returns 401."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "email": "test@example.com",
            "hashed_password": hash_password("ValidPassword123!"),
            "is_active": 1,
        }
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = self.client.post(
            "/api/auth/reset-password",
            json={
                "email": "test@example.com",
                "current_password": "WrongPassword123!",
                "new_password": "BrandNewSecretPassword456!",
            },
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("Current password or email is incorrect", res.json().get("detail", ""))

    @patch("routers.auth.get_connection")
    def test_reset_password_success(self, mock_db):
        """Reset password with correct current password and valid new password updates hash."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "email": "test@example.com",
            "hashed_password": hash_password("ValidPassword123!"),
            "is_active": 1,
        }
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = self.client.post(
            "/api/auth/reset-password",
            json={
                "email": "test@example.com",
                "current_password": "ValidPassword123!",
                "new_password": "BrandNewSecretPassword456!",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("Password updated successfully", res.json().get("message", ""))



    # ──────────────────────────────────────────────────────────
    # 2. RBAC & Privileges
    # ──────────────────────────────────────────────────────────
    def test_normal_user_cannot_post_canonical_data(self):
        """A user with role='user' must be blocked from POST /api/data with 403 Forbidden."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        csrf = generate_csrf_token()
        
        res = self.client.post(
            "/api/data",
            json=[{"Symbol": "TEST", "Timestamp": "2026-08-10", "Open": 100, "High": 105, "Low": 95, "Close": 102, "Volume": 1000}],
            headers={"x-csrf-token": csrf},
            cookies={"csrf_token": csrf},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Administrative privileges required", res.json().get("detail", ""))

    @patch("routers.stocks.get_connection")
    def test_admin_user_can_post_canonical_data(self, mock_db):
        """An admin user with role='admin' can post data successfully."""
        app.dependency_overrides[get_current_user] = lambda: self.admin_data
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        csrf = generate_csrf_token()
        res = self.client.post(
            "/api/data",
            json=[{"Symbol": "TEST", "Timestamp": "2026-08-10", "Open": 100, "High": 105, "Low": 95, "Close": 102, "Volume": 1000}],
            headers={"x-csrf-token": csrf},
            cookies={"csrf_token": csrf},
        )
        self.assertEqual(res.status_code, 200)

    # ──────────────────────────────────────────────────────────
    # 3. CSRF Protection
    # ──────────────────────────────────────────────────────────
    def test_state_changing_post_without_csrf_is_blocked(self):
        """State-changing POST without CSRF headers must return 403 Forbidden."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        res = self.client.post(
            "/api/scans/run",
            json={"conditions": [{"field": "Hammer", "operator": "=="}], "logic": "AND"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("CSRF token missing or invalid", res.json().get("detail", ""))


    def test_state_changing_post_with_mismatched_csrf_is_blocked(self):
        """CSRF token mismatch between header and cookie must return 403 Forbidden."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        res = self.client.post(
            "/api/scans/run",
            json={"conditions": [{"field": "Hammer", "operator": "=="}], "logic": "AND"},
            headers={"x-csrf-token": "attacker-token-value"},
            cookies={"csrf_token": "legitimate-token-value"},
        )
        self.assertEqual(res.status_code, 403)

    @patch("routers.scans.run_scan")
    def test_state_changing_post_with_matching_csrf_succeeds(self, mock_scan):
        """POST with matching CSRF cookie and header should pass verification."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        mock_scan.return_value = []
        csrf = generate_csrf_token()
        res = self.client.post(
            "/api/scans/run",
            json={"conditions": [{"field": "Hammer", "operator": "=="}], "logic": "AND"},
            headers={"x-csrf-token": csrf},
            cookies={"csrf_token": csrf},
        )
        self.assertEqual(res.status_code, 200)

    # ──────────────────────────────────────────────────────────
    # 4. Password Security
    # ──────────────────────────────────────────────────────────
    def test_registration_rejects_short_password(self):
        """Passwords with fewer than 12 characters must be rejected with 422."""
        res = self.client.post(
            "/api/auth/register",
            json={"name": "Test User", "email": "short@example.com", "password": "Short1!"},
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("at least 12 characters", str(res.json()))

    def test_registration_rejects_purely_alphabetic_password(self):
        """Passwords without at least one digit must be rejected."""
        res = self.client.post(
            "/api/auth/register",
            json={"name": "Test User", "email": "nodigit@example.com", "password": "AllLettersNoDigitsHere"},
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("at least one digit", str(res.json()))

    # ──────────────────────────────────────────────────────────
    # 5. Rate Limiting
    # ──────────────────────────────────────────────────────────
    def test_login_rate_limiter_triggers_429(self):
        """Excessive failed attempts from same IP / target must trigger rate limiting with 429."""
        custom_store = InMemorySlidingWindowStore()
        limiter = LoginRateLimiter(max_ip_attempts=3, ip_window_seconds=60, store=custom_store)
        
        mock_req = MagicMock()
        mock_req.client.host = "192.168.1.100"
        mock_req.headers = {}
        
        # 3 attempts should be allowed
        limiter.check_and_record(mock_req)
        limiter.check_and_record(mock_req)
        limiter.check_and_record(mock_req)
        
        # 4th attempt should raise HTTPException(429)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            limiter.check_and_record(mock_req)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Too many login attempts", ctx.exception.detail)

    # ──────────────────────────────────────────────────────────
    # 6. API Resource Limits & Parameter Bounds
    # ──────────────────────────────────────────────────────────
    def test_scan_conditions_limit_enforced(self):
        """Scan requests with more than 20 conditions must be rejected."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        csrf = generate_csrf_token()
        excessive_conditions = [{"field": "Hammer", "operator": "=="}] * 25
        res = self.client.post(
            "/api/scans/run",
            json={"conditions": excessive_conditions, "logic": "AND"},
            headers={"x-csrf-token": csrf},
            cookies={"csrf_token": csrf},
        )
        self.assertEqual(res.status_code, 422)

    def test_historical_data_date_range_limit_enforced(self):
        """Queries for historical data spanning more than 5 years must be rejected."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        res = self.client.get(
            "/api/data",
            params={"symbol": "AAPL", "start_date": "2015-01-01", "end_date": "2026-01-01"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("5 years", res.json().get("detail", ""))

    def test_signal_scanner_limit_capped(self):
        """GET /api/signal-scanner limit query parameter capped at 5000."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        res = self.client.get("/api/signal-scanner", params={"limit": 10000})
        self.assertEqual(res.status_code, 422)

    def test_ohlcv_days_capped(self):
        """GET /api/ohlcv/{symbol} days capped at 2520 (~10 trading years)."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        res = self.client.get("/api/ohlcv/AAPL", params={"days": 5000})
        self.assertEqual(res.status_code, 422)

    # ──────────────────────────────────────────────────────────
    # 7. Security Headers & Global Error Handling
    # ──────────────────────────────────────────────────────────
    def test_security_headers_present_on_responses(self):
        """Core security headers must be included on HTTP responses."""
        res = self.client.get("/api/signals")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("default-src 'self'", res.headers.get("Content-Security-Policy", ""))

    @patch("routers.stocks.get_connection")
    def test_database_exceptions_do_not_leak_raw_sql_or_credentials(self, mock_db):
        """Database errors must return generic 500 message without revealing internal SQL."""
        app.dependency_overrides[get_current_user] = lambda: self.user_data
        mock_db.side_effect = Exception("Access denied for user 'root'@'localhost' (using password: YES)")
        res = self.client.get("/api/symbols")
        self.assertEqual(res.status_code, 500)
        detail = res.json().get("detail", "")
        self.assertNotIn("root", detail)
        self.assertNotIn("password", detail)
        self.assertIn("Failed to fetch symbols", detail)


if __name__ == "__main__":
    unittest.main()

