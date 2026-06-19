import json
import hashlib
import logging
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.config import get_app_settings, get_config, get_userData, normalize_unique_id, repo_root, save_config

logger = logging.getLogger(__name__)

TASK_ALREADY_RUNNING = -2

TASK_SCHEDULE_MARKERS = (
    "docker compose run --rm task",
    "docker compose run --rm douyin",
    "main.py --doTask",
)
HOST_CRONTAB_PATH = Path("/host-spool-cron/root")
WINDOWED_SCHEDULE_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})/(\d+)m$", re.IGNORECASE)


def running_in_container():
    return Path("/.dockerenv").exists()


def compose_root():
    settings = get_app_settings()
    raw = settings.get("compose_root") or ""
    if raw:
        p = Path(raw)
        if (p / "docker-compose.yml").exists():
            return p
    # Docker-out-of-Docker: the compose file lives on the host at
    # /opt/douyin-sparkflow but is not always bind-mounted into /app.
    for candidate in [
        Path("/opt/douyin-sparkflow"),
        repo_root().parent,
        repo_root(),
    ]:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    # Fallback
    return Path(raw) if raw else repo_root()


def compose_file_path():
    path = compose_root() / "docker-compose.yml"
    return path if path.exists() else None


def compose_command(*args):
    compose_file = compose_file_path()
    base = ["docker", "compose"]
    if compose_file:
        base.extend(["-f", str(compose_file)])
    base.extend(args)
    return base


def _pid_is_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_lock_pid(raw):
    try:
        return int(str(raw or "").strip().splitlines()[0])
    except (IndexError, TypeError, ValueError):
        return None


def task_run_lock_status():
    lock_path = repo_root() / "logs" / "task.run.lock"
    if not lock_path.exists():
        return {"running": False, "path": str(lock_path), "pid": None, "ageSeconds": 0, "staleRemoved": False}

    raw = lock_path.read_text(encoding="utf-8", errors="ignore")
    pid = _parse_lock_pid(raw)
    try:
        age_seconds = max(0, int(datetime.now(timezone.utc).timestamp() - lock_path.stat().st_mtime))
    except OSError:
        return {"running": False, "path": str(lock_path), "pid": pid, "ageSeconds": 0, "staleRemoved": False}

    if pid is not None and not _pid_is_alive(pid):
        try:
            lock_path.unlink()
            logger.warning("Removed stale task run lock owned by missing pid=%s", pid)
            return {"running": False, "path": str(lock_path), "pid": pid, "ageSeconds": age_seconds, "staleRemoved": True}
        except FileNotFoundError:
            return {"running": False, "path": str(lock_path), "pid": pid, "ageSeconds": age_seconds, "staleRemoved": True}

    if pid is None and age_seconds > 7200:
        try:
            lock_path.unlink()
            logger.warning("Removed stale unreadable task run lock contents=%r", raw[:80])
            return {"running": False, "path": str(lock_path), "pid": None, "ageSeconds": age_seconds, "staleRemoved": True}
        except FileNotFoundError:
            return {"running": False, "path": str(lock_path), "pid": None, "ageSeconds": age_seconds, "staleRemoved": True}

    return {"running": True, "path": str(lock_path), "pid": pid, "ageSeconds": age_seconds, "staleRemoved": False}


def build_task_run_spec():
    if running_in_container():
        return [sys.executable, "main.py", "--doTask"], repo_root()
    if compose_file_path():
        return compose_command("run", "--rm", "task"), compose_root()
    return [sys.executable, "main.py", "--doTask"], repo_root()


def _env_shell_prefix(extra_env=None):
    parts = []
    for key, value in (extra_env or {}).items():
        parts.append(f"{key}={shlex.quote(str(value))}")
    return " ".join(parts)


def _with_env_prefix(command, extra_env=None):
    env_prefix = _env_shell_prefix(extra_env)
    return f"env {env_prefix} {command}" if env_prefix else command


def _compose_env_args(extra_env=None):
    parts = []
    for key, value in (extra_env or {}).items():
        parts.extend(["-e", f"{key}={value}"])
    return " ".join(shlex.quote(part) for part in parts)


def build_scheduled_task_command(extra_env=None, trigger_label="scheduled send"):
    if running_in_container():
        task_command = _with_env_prefix("python main.py --doTask", extra_env)
        return (
            "/bin/bash -lc 'timestamp=$(date -Iseconds); "
            f"echo \"[AUTO_TRIGGER] $timestamp {trigger_label} start\"; "
            "container=$(docker ps --format \"{{.Names}}\" | "
            "grep -E \"^(douyin-web-hostfix|douyin-web)$\" | head -n 1); "
            "if [ -z \"$container\" ]; then "
            "echo \"[AUTO_TRIGGER] $timestamp no matching container found\"; "
            "exit 1; "
            "fi; "
            "echo \"[AUTO_TRIGGER] $timestamp container=$container\"; "
            "docker exec \"$container\" sh -lc "
            f"\"cd /app && {task_command}\"'"
        )
    if compose_file_path():
        compose_root_quoted = shlex.quote(str(compose_root()))
        compose_env_args = _compose_env_args(extra_env)
        compose_env_suffix = f" {compose_env_args}" if compose_env_args else ""
        return (
            "/bin/bash -lc "
            f"'echo \"[AUTO_TRIGGER] $(date -Iseconds) compose {trigger_label} start\"; "
            f"cd {compose_root_quoted} && /usr/bin/docker compose run --rm{compose_env_suffix} task'"
        )
    repo_root_quoted = shlex.quote(str(repo_root()))
    python_quoted = shlex.quote(sys.executable)
    task_command = _with_env_prefix(f"{python_quoted} main.py --doTask", extra_env)
    return (
        "/bin/bash -lc "
        f"'echo \"[AUTO_TRIGGER] $(date -Iseconds) local {trigger_label} start\"; "
        f"cd {repo_root_quoted} && {task_command}'"
    )


def build_unsent_fallback_task_command():
    return build_scheduled_task_command(
        {
            "SPARKFLOW_MANUAL_RUN": "1",
            "SPARKFLOW_MANUAL_UNSENT_ONLY": "1",
            "PYTHONUNBUFFERED": "1",
        },
        trigger_label="unsent fallback",
    )


def run_command(args, cwd=None, timeout=120, check=False):
    """Run a command and return the CompletedProcess.

    ``check`` defaults to False so callers can inspect the result without
    crashing when the command is unavailable (e.g. docker not installed).
    """
    try:
        return subprocess.run(
            args,
            cwd=str(cwd or compose_root()),
            check=check,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.warning("Command not found: %s", args[0] if args else args)
        return _empty_result()
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out: %s", args)
        return _empty_result()
    except subprocess.CalledProcessError as exc:
        logger.warning("Command failed (rc=%s): %s", exc.returncode, args)
        return _empty_result(stderr=exc.stderr or "")


def _empty_result(stdout="", stderr=""):
    """Return a fake CompletedProcess for graceful degradation."""
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)


def run_background_command(args, log_path, cwd=None, env=None):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    process = subprocess.Popen(
        args,
        cwd=str(Path(cwd) if cwd else compose_root()),
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=child_env,
    )
    handle.close()
    return process.pid


def get_container_status():
    try:
        result = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--format",
                "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.State}}\t{{.RunningFor}}\t{{.Labels}}",
            ],
            timeout=15,
        )
        rows = []
        for raw_line in (result.stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t", 5)
            while len(parts) < 6:
                parts.append("")
            name, image, status, state, running_for, labels = parts
            rows.append(
                {
                    "Names": name,
                    "Image": image,
                    "Status": status,
                    "State": state,
                    "RunningFor": running_for,
                    "Labels": labels,
                }
            )
        return rows
    except Exception as exc:
        logger.warning("get_container_status failed: %s", exc)
        return []


class contextlib_suppress_json:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is json.JSONDecodeError


def get_task_container_rows():
    try:
        rows = get_container_status()
        interesting_names = {"douyin-web-hostfix", "douyin-web", "douyin-task"}
        return [row for row in rows if row.get("Names") in interesting_names]
    except Exception as exc:
        logger.warning("get_task_container_rows failed: %s", exc)
        return []


def run_task_now(*, unsent_only=False, failed_only=False):
    try:
        lock_status = task_run_lock_status()
        if lock_status.get("running"):
            logger.info(
                "Refusing to start manual task because task lock is active pid=%s age=%ss",
                lock_status.get("pid"),
                lock_status.get("ageSeconds"),
            )
            return TASK_ALREADY_RUNNING

        log_file = Path(get_app_settings().get("ops_log_file") or "/var/log/douyin-sparkflow.log")
        command, cwd = build_task_run_spec()
        run_env = {
            "SPARKFLOW_MANUAL_RUN": "1",
            "PYTHONUNBUFFERED": "1",
        }
        if failed_only:
            run_env["SPARKFLOW_MANUAL_FAILED_ONLY"] = "1"
        elif unsent_only:
            run_env["SPARKFLOW_MANUAL_UNSENT_ONLY"] = "1"
        return run_background_command(
            command,
            log_file,
            cwd=cwd,
            env=run_env,
        )
    except Exception as exc:
        import traceback
        Path("task_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logger.error("run_task_now failed: %s", exc)
        return -1


def run_failed_retry_now():
    return run_task_now(failed_only=True)


def run_unsent_retry_now():
    return run_task_now(unsent_only=True)


def refresh_proxy():
    try:
        script = Path(get_app_settings().get("proxy_refresh_script") or "")
        if script.exists():
            return run_command(["bash", str(script)], timeout=120)
        return run_command(compose_command("restart", "proxy"), timeout=120)
    except Exception as exc:
        logger.error("refresh_proxy failed: %s", exc)
        return _empty_result(stderr=str(exc))


def restart_proxy():
    try:
        return run_command(compose_command("restart", "proxy"), timeout=120)
    except Exception as exc:
        logger.error("restart_proxy failed: %s", exc)
        return _empty_result(stderr=str(exc))


def read_log_tail(lines=200):
    log_path = Path(get_app_settings().get("ops_log_file") or "/var/log/douyin-sparkflow.log")
    if not log_path.exists():
        return ""
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def read_crontab():
    if running_in_container() and HOST_CRONTAB_PATH.exists():
        return HOST_CRONTAB_PATH.read_text(encoding="utf-8", errors="replace")
    try:
        result = subprocess.run(["crontab", "-l"], text=True, capture_output=True, timeout=10)
        if result.returncode != 0:
            return ""
        return result.stdout
    except Exception as exc:
        logger.warning("read_crontab failed: %s", exc)
        return ""


def _format_window_schedule(window_config):
    return (
        f"{int(window_config['startHour']):02d}:00-"
        f"{int(window_config['endHour']):02d}:00/"
        f"{int(window_config['scheduleIntervalMinutes'])}m"
    )


def parse_schedule_string(time_string):
    raw = str(time_string or "").strip()
    match = WINDOWED_SCHEDULE_RE.fullmatch(raw)
    if match:
        start_hour, start_minute, end_hour, end_minute, interval = [int(part) for part in match.groups()]
        if start_minute != 0 or end_minute != 0:
            raise ValueError("Window schedule must use whole hours, e.g. 10:00-18:00/10m")
        if start_hour not in range(24) or end_hour not in range(24) or end_hour <= start_hour:
            raise ValueError("Window schedule is out of range")
        if interval not in range(1, 60):
            raise ValueError("Window schedule interval must be between 1 and 59 minutes")
        return {
            "mode": "window",
            "startHour": start_hour,
            "endHour": end_hour,
            "scheduleIntervalMinutes": interval,
        }

    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise ValueError("Time must use HH:MM or HH:00-HH:00/10m format")
    hour, minute = [int(part) for part in raw.split(":", 1)]
    if hour not in range(24) or minute not in range(60):
        raise ValueError("Time is out of range")
    return {"mode": "fixed", "hour": hour, "minute": minute}


def validate_time_string(time_string):
    parsed = parse_schedule_string(time_string)
    if parsed["mode"] != "fixed":
        raise ValueError("Time must use HH:MM format")
    return parsed["hour"], parsed["minute"]


def replace_douyin_cron_schedule(crontab_text, time_string):
    schedule = parse_schedule_string(time_string)
    scheduled_command = build_scheduled_task_command()
    fallback_command = build_unsent_fallback_task_command()
    updated = []

    for raw_line in crontab_text.splitlines():
        line = raw_line.rstrip("\n")
        if any(marker in line for marker in TASK_SCHEDULE_MARKERS):
            continue
        updated.append(line)

    if schedule["mode"] == "window":
        updated.append(
            f"*/{schedule['scheduleIntervalMinutes']} {schedule['startHour']}-{schedule['endHour'] - 1} * * * "
            f"{scheduled_command} >> /var/log/douyin-sparkflow.log 2>&1"
        )
        updated.append(
            f"0 {schedule['endHour']} * * * "
            f"{scheduled_command} >> /var/log/douyin-sparkflow.log 2>&1"
        )
        updated.append(
            f"{schedule['scheduleIntervalMinutes']} {schedule['endHour']} * * * "
            f"{fallback_command} >> /var/log/douyin-sparkflow.log 2>&1"
        )
    else:
        updated.append(
            f"{schedule['minute']} {schedule['hour']} * * * "
            f"{scheduled_command} >> /var/log/douyin-sparkflow.log 2>&1"
        )

    normalized = "\n".join(line for line in updated if line.strip())
    if normalized:
        normalized += "\n"
    return normalized


def persist_schedule_config(time_string):
    parsed = parse_schedule_string(time_string)
    config = get_config(force_reload=True)
    window = dict(config.get("dailySendWindow") or {})
    if parsed["mode"] == "window":
        window.update(
            {
                "enabled": True,
                "startHour": parsed["startHour"],
                "endHour": parsed["endHour"],
                "scheduleIntervalMinutes": parsed["scheduleIntervalMinutes"],
            }
        )
    else:
        window.update({"enabled": False})
    config["dailySendWindow"] = window
    save_config(config)


def update_daily_schedule(time_string):
    persist_schedule_config(time_string)
    current = read_crontab()
    updated = replace_douyin_cron_schedule(current, time_string)
    if running_in_container() and HOST_CRONTAB_PATH.parent.exists():
        try:
            HOST_CRONTAB_PATH.write_text(updated, encoding="utf-8")
            return subprocess.CompletedProcess(args=["write-host-crontab"], returncode=0, stdout="", stderr="")
        except Exception as exc:
            logger.error("update_daily_schedule failed: %s", exc)
            return _empty_result(stderr=str(exc))
    try:
        process = subprocess.run(["crontab", "-"], input=updated, text=True, capture_output=True, check=True, timeout=10)
        return process
    except Exception as exc:
        logger.error("update_daily_schedule failed: %s", exc)
        return _empty_result(stderr=str(exc))


def current_daily_schedule():
    config = get_config(force_reload=True)
    window = dict(config.get("dailySendWindow") or {})
    if window.get("enabled"):
        try:
            return _format_window_schedule(window)
        except Exception:
            logger.warning("current_daily_schedule found invalid dailySendWindow=%s", window)

    for line in read_crontab().splitlines():
        if any(marker in line for marker in TASK_SCHEDULE_MARKERS):
            parts = line.split(maxsplit=5)
            if len(parts) >= 2:
                if parts[0].isdigit() and parts[1].isdigit():
                    minute = int(parts[0])
                    hour = int(parts[1])
                    return f"{hour:02d}:{minute:02d}"
                return f"{parts[1]}:{parts[0]}"
    return ""


def _schedule_timezone():
    timezone_name = (
        str(os.getenv("SPARKFLOW_TIMEZONE") or "").strip()
        or str(os.getenv("TZ") or "").strip()
        or "Asia/Shanghai"
    )
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        return datetime.now().astimezone().tzinfo


def _normalize_send_window():
    raw = dict(get_config(force_reload=True).get("dailySendWindow") or {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "startHour": int(raw.get("startHour", 10)),
        "endHour": int(raw.get("endHour", 18)),
        "scheduleIntervalMinutes": max(1, int(raw.get("scheduleIntervalMinutes", 10))),
    }


def _parse_sent_at(raw_value, local_tz):
    if not raw_value:
        return None
    raw = str(raw_value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz)


def _account_identity(user):
    return str(user.get("unique_id") or user.get("username") or "unknown").strip()


def _coerce_attempt_count(entry):
    try:
        return int(dict(entry or {}).get("attemptCount") or 0)
    except (TypeError, ValueError):
        return 0


def _account_failure_pause_after_attempts():
    raw_value = str(os.getenv("SPARKFLOW_ACCOUNT_FAILURE_PAUSE_AFTER_ATTEMPTS") or "2").strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 2


def _account_failure_entry_today(account, now):
    entry = dict(account.get("account_failure") or {})
    last_attempt_at = _parse_sent_at(entry.get("lastAttemptAt"), now.tzinfo)
    if last_attempt_at and last_attempt_at.date() == now.date():
        entry["lastAttemptAt"] = last_attempt_at.isoformat(timespec="seconds")
        first_attempt_at = _parse_sent_at(entry.get("firstAttemptAt"), now.tzinfo)
        if first_attempt_at:
            entry["firstAttemptAt"] = first_attempt_at.isoformat(timespec="seconds")
        entry["attemptCount"] = _coerce_attempt_count(entry)
        entry["affectedTargets"] = list(entry.get("affectedTargets") or [])
        return entry
    return {}


def _normalize_friend_index_key(value):
    raw = unicodedata.normalize("NFKC", str(value or ""))
    for token in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        raw = raw.replace(token, "")
    raw = raw.replace("\xa0", " ")
    return " ".join(raw.split()).strip()


def _friend_index_status(account, target_name):
    friend_index = dict(account.get("friend_index") or {})
    entry = dict(friend_index.get(_normalize_friend_index_key(target_name)) or {})
    return {
        "seen": bool(entry),
        "visibleName": str(entry.get("visibleName") or ""),
        "stableKeys": list(entry.get("stableKeys") or []),
        "lastSeenAt": str(entry.get("lastSeenAt") or ""),
    }


def _account_blocked_target_status(item, account_failure):
    blocked_item = dict(item)
    affected_targets = set(account_failure.get("affectedTargets") or [])
    blocked_item.update(
        {
            "status": "account_blocked",
            "category": str(account_failure.get("category") or ""),
            "reason": str(account_failure.get("reason") or ""),
            "attemptCount": _coerce_attempt_count(account_failure),
            "lastAttemptAt": str(account_failure.get("lastAttemptAt") or ""),
            "accountFailureAffected": blocked_item.get("target") in affected_targets,
        }
    )
    return blocked_item


def _scheduled_send_time(user, target_name, send_window, now):
    window_minutes = max(1, (send_window["endHour"] - send_window["startHour"]) * 60)
    start_of_window = now.replace(
        hour=send_window["startHour"],
        minute=0,
        second=0,
        microsecond=0,
    )
    seed = f"{now.date().isoformat()}|{_account_identity(user)}|{target_name}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    offset_minutes = int.from_bytes(digest[:8], "big") % window_minutes
    return start_of_window + timedelta(minutes=offset_minutes)


def _build_target_status(account, target_name, now, send_window):
    history = dict(account.get("message_history") or {})
    failure_queue = dict(account.get("failure_queue") or {})
    friend_index = _friend_index_status(account, target_name)

    history_entry = history.get(target_name) or {}
    sent_at = _parse_sent_at(history_entry.get("sentAt"), now.tzinfo)
    if sent_at and sent_at.date() == now.date():
        return {
            "target": target_name,
            "status": "sent",
            "message": str(history_entry.get("message") or ""),
            "sentAt": sent_at.isoformat(timespec="seconds"),
            "lastAttemptAt": "",
            "category": "",
            "reason": "",
            "attemptCount": 0,
            "scheduledAt": "",
            "friendIndex": friend_index,
        }

    failure_entry = failure_queue.get(target_name) or {}
    last_attempt_at = _parse_sent_at(failure_entry.get("lastAttemptAt"), now.tzinfo)
    if last_attempt_at and last_attempt_at.date() == now.date():
        return {
            "target": target_name,
            "status": "failed",
            "message": str(failure_entry.get("message") or ""),
            "sentAt": "",
            "lastAttemptAt": last_attempt_at.isoformat(timespec="seconds"),
            "category": str(failure_entry.get("category") or ""),
            "reason": str(failure_entry.get("reason") or ""),
            "attemptCount": int(failure_entry.get("attemptCount") or 0),
            "scheduledAt": "",
            "friendIndex": friend_index,
        }

    scheduled_at = None
    if send_window.get("enabled"):
        scheduled_at = _scheduled_send_time(account, target_name, send_window, now)
        if scheduled_at > now:
            return {
                "target": target_name,
                "status": "pending",
                "message": "",
                "sentAt": "",
                "lastAttemptAt": "",
                "category": "",
                "reason": "",
                "attemptCount": 0,
                "scheduledAt": scheduled_at.isoformat(timespec="seconds"),
                "friendIndex": friend_index,
            }

    return {
        "target": target_name,
        "status": "unprocessed",
        "message": "",
        "sentAt": "",
        "lastAttemptAt": "",
        "category": "",
        "reason": "",
        "attemptCount": 0,
        "scheduledAt": scheduled_at.isoformat(timespec="seconds") if scheduled_at else "",
        "friendIndex": friend_index,
    }


def get_send_console_snapshot():
    accounts = [account for account in get_userData(force_reload=True) if account.get("enabled", True)]
    send_window = _normalize_send_window()
    now = datetime.now(_schedule_timezone())

    summary = {
        "enabled_accounts": len(accounts),
        "total_targets": 0,
        "today_sent_targets": 0,
        "today_failed_targets": 0,
        "today_pending_targets": 0,
        "today_unprocessed_targets": 0,
        "today_account_blocked_targets": 0,
        "today_remaining_targets": 0,
        "today_account_failures": 0,
        "today_account_paused": 0,
    }
    account_rows = []
    account_failure_pause_after = _account_failure_pause_after_attempts()

    for account in accounts:
        configured_targets = list(account.get("targets") or [])
        statuses = [_build_target_status(account, target_name, now, send_window) for target_name in configured_targets]
        sent_targets = [item for item in statuses if item["status"] == "sent"]
        failed_targets = [item for item in statuses if item["status"] == "failed"]
        account_failure = _account_failure_entry_today(account, now)
        account_paused = bool(account_failure and _coerce_attempt_count(account_failure) >= account_failure_pause_after)
        account_blocked_targets = []
        if account_paused:
            account_blocked_targets = [
                _account_blocked_target_status(item, account_failure)
                for item in statuses
                if item["status"] in {"pending", "unprocessed"}
            ]
            pending_targets = []
            unprocessed_targets = []
        else:
            pending_targets = [item for item in statuses if item["status"] == "pending"]
            unprocessed_targets = [item for item in statuses if item["status"] == "unprocessed"]
        friend_index_meta = dict(account.get("friend_index_meta") or {})
        friend_index_last_scan_at = _parse_sent_at(friend_index_meta.get("lastScanAt"), now.tzinfo)
        if friend_index_last_scan_at:
            friend_index_meta["lastScanAt"] = friend_index_last_scan_at.isoformat(timespec="seconds")
        friend_index_meta["missingTargets"] = list(friend_index_meta.get("missingTargets") or [])
        friend_index_meta["lastScanComplete"] = bool(friend_index_meta.get("lastScanComplete"))
        try:
            friend_index_meta["scannedCount"] = int(friend_index_meta.get("scannedCount") or 0)
        except (TypeError, ValueError):
            friend_index_meta["scannedCount"] = 0

        summary["total_targets"] += len(configured_targets)
        summary["today_sent_targets"] += len(sent_targets)
        summary["today_failed_targets"] += len(failed_targets)
        summary["today_pending_targets"] += len(pending_targets)
        summary["today_unprocessed_targets"] += len(unprocessed_targets)
        summary["today_account_blocked_targets"] += len(account_blocked_targets)
        summary["today_remaining_targets"] += (
            len(failed_targets)
            + len(pending_targets)
            + len(unprocessed_targets)
            + len(account_blocked_targets)
        )
        if account_failure:
            summary["today_account_failures"] += 1
        if account_paused:
            summary["today_account_paused"] += 1

        account_rows.append(
            {
                "unique_id": str(account.get("unique_id") or ""),
                "username": account.get("username") or "",
                "total_targets": len(configured_targets),
                "sent_targets": sent_targets,
                "failed_targets": failed_targets,
                "pending_targets": pending_targets,
                "unprocessed_targets": unprocessed_targets,
                "account_blocked_targets": account_blocked_targets,
                "last_failure_reason": failed_targets[0]["reason"] if failed_targets else "",
                "failure_queue": dict(account.get("failure_queue") or {}),
                "account_failure": account_failure,
                "account_paused": account_paused,
                "account_failure_pause_after": account_failure_pause_after,
                "friend_index_meta": friend_index_meta,
                "friend_index_count": len(dict(account.get("friend_index") or {})),
            }
        )

    return {
        "now": now.isoformat(timespec="seconds"),
        "summary": summary,
        "accounts": account_rows,
    }


def _check_image_present():
    """Return True if the douyin-sparkflow:local image exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "douyin-sparkflow:local"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_ops_snapshot():
    """Collect operational metrics for the dashboard.

    Every external call is individually guarded so the dashboard always
    renders, even when Docker or crontab are not available.
    """
    return {
        "compose_root": str(compose_root()),
        "compose_file": str(compose_file_path() or ""),
        "containers": get_container_status(),
        "task_containers": get_task_container_rows(),
        "send_console": get_send_console_snapshot(),
        "task_lock": task_run_lock_status(),
        "daily_schedule": current_daily_schedule(),
        "crontab": read_crontab(),
        "log_tail": read_log_tail(120),
        "image_present": _check_image_present(),
    }
