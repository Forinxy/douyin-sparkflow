import errno
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core import tasks
from webui import app as app_module
from webui import ops


class WebUiSafetyTests(unittest.TestCase):
    def test_windows_invalid_pid_probe_is_treated_as_dead(self):
        error = OSError(errno.EINVAL, "invalid pid")
        error.winerror = 87
        with patch.object(ops.os, "kill", side_effect=error):
            self.assertFalse(ops._pid_is_alive(999999))
        with patch.object(tasks.os, "kill", side_effect=error):
            self.assertFalse(tasks._pid_is_alive(999999))

    def test_stale_lock_inspection_does_not_delete_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "logs" / "task.run.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("99999999\n", encoding="utf-8")
            old = time.time() - 10800
            os.utime(lock_path, (old, old))

            with patch.object(ops, "repo_root", return_value=root):
                status = ops.task_run_lock_status()

            self.assertTrue(lock_path.exists())
            self.assertTrue(status["stale"])
            self.assertFalse(status["running"])
            self.assertEqual("owner_pid_missing", status["staleReason"])

    def test_overview_snapshot_excludes_sensitive_payloads(self):
        send_console = {
            "now": "2026-07-10T22:00:00+08:00",
            "summary": {
                "enabled_accounts": 1,
                "total_targets": 2,
                "today_confirmed_targets": 1,
                "today_unconfirmed_targets": 1,
                "today_failed_targets": 0,
                "today_account_blocked_targets": 0,
                "today_attention_targets": 1,
                "today_pending_targets": 0,
                "today_unprocessed_targets": 0,
                "today_remaining_targets": 1,
                "today_warning_count": 0,
                "last_confirmed_at": "2026-07-10T21:00:00+08:00",
                "all_confirmed": False,
            },
            "accounts": [
                {
                    "unique_id": "account-1",
                    "username": "Account",
                    "state": "attention",
                    "total_targets": 2,
                    "confirmed_targets": [{"message": "secret message"}],
                    "attention_count": 1,
                    "pending_count": 0,
                    "last_confirmed_at": "2026-07-10T21:00:00+08:00",
                }
            ],
        }

        with (
            patch.object(ops, "get_send_console_snapshot", return_value=send_console),
            patch.object(
                ops,
                "get_schedule_snapshot",
                return_value={"label": "10:00-18:00/20m", "nextTriggerAt": ""},
            ),
            patch.object(
                ops,
                "task_run_lock_status",
                return_value={"running": False, "stale": False, "ageSeconds": 0},
            ),
        ):
            payload = ops.get_overview_snapshot()

        serialized = repr(payload)
        self.assertNotIn("secret message", serialized)
        self.assertNotIn("cookies", serialized)
        self.assertNotIn("serverReceipt", serialized)
        self.assertNotIn("reason", serialized)
        self.assertEqual(1, payload["summary"]["attention"])

    def test_primary_pages_and_local_icons_render(self):
        client = TestClient(app_module.app, raise_server_exceptions=False)
        self.assertEqual(200, client.get("/login").status_code)
        self.assertEqual(200, client.get("/static/lucide.min.js").status_code)

        with patch.object(app_module, "current_user", return_value="admin"):
            for path in ("/", "/ops/send-console", "/ops/logs"):
                response = client.get(path)
                self.assertEqual(200, response.status_code, path)
                self.assertEqual("no-store", response.headers["cache-control"])

    def test_login_desktop_urls_honor_container_environment(self):
        with (
            patch.dict(
                os.environ,
                {
                    "SPARKFLOW_LOGIN_DESKTOP_API_URL": "http://login-desktop:18090",
                    "SPARKFLOW_LOGIN_DESKTOP_PUBLIC_URL": "http://127.0.0.1:8788/vnc.html",
                },
            ),
            patch.object(app_module, "get_app_settings", return_value={}),
        ):
            self.assertEqual("http://login-desktop:18090", app_module.login_desktop_api_url())
            request = type("Request", (), {"url": type("Url", (), {"hostname": "example", "scheme": "http"})()})()
            self.assertEqual(
                "http://127.0.0.1:8788/vnc.html",
                app_module.login_desktop_public_url(request),
            )

    def test_schedule_sync_writes_configured_window_to_shared_spool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cron_path = Path(temp_dir) / "root"
            with (
                patch.object(ops, "HOST_CRONTAB_PATH", cron_path),
                patch.object(ops, "running_in_container", return_value=True),
                patch.object(ops, "read_crontab", return_value=""),
                patch.object(
                    ops,
                    "get_config",
                    return_value={
                        "dailySendWindow": {
                            "enabled": True,
                            "startHour": 10,
                            "endHour": 18,
                            "scheduleIntervalMinutes": 20,
                        }
                    },
                ),
            ):
                result = ops.sync_daily_schedule_from_config()

            self.assertEqual(0, result.returncode)
            text = cron_path.read_text(encoding="utf-8")
            self.assertIn("*/20 10-17 * * *", text)
            self.assertIn("0 18 * * *", text)
            self.assertIn("20 18 * * *", text)
            self.assertIn("docker exec", text)

    def test_overview_api_requires_authentication_and_disables_cache(self):
        client = TestClient(app_module.app)
        response = client.get("/api/ops/overview")

        self.assertEqual(401, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])

        with (
            patch.object(app_module, "current_user", return_value="admin"),
            patch.object(
                app_module,
                "get_overview_snapshot",
                return_value={
                    "now": "2026-07-10T22:00:00+08:00",
                    "schedule": {},
                    "task": {},
                    "summary": {},
                    "accounts": [],
                },
            ),
        ):
            response = client.get("/api/ops/overview")

        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_public_settings_and_template_do_not_expose_server_password(self):
        with patch.object(
            app_module,
            "get_app_settings",
            return_value={
                "server_host": "example",
                "server_username": "root",
                "server_password": "secret",
                "session_secret": "secret",
                "admin_password_hash": "hash",
                "compose_root": "/opt/app",
                "ui_port": 8787,
                "login_desktop_api_url": "http://127.0.0.1:18090",
            },
        ):
            public = app_module.public_app_settings()

        self.assertNotIn("server_password", public)
        self.assertNotIn("session_secret", public)
        dashboard = (
            Path(app_module.TEMPLATES_DIR) / "dashboard.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("server_password", dashboard)
        self.assertNotIn("server_username", dashboard)
        self.assertNotIn("server_host", dashboard)


if __name__ == "__main__":
    unittest.main()
