import json
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from utils.config import get_app_settings, get_config, repo_root, save_config

logger = logging.getLogger(__name__)

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


def build_task_run_spec():
    if running_in_container():
        return [sys.executable, "main.py", "--doTask"], repo_root()
    if compose_file_path():
        return compose_command("run", "--rm", "task"), compose_root()
    return [sys.executable, "main.py", "--doTask"], repo_root()


def build_scheduled_task_command():
    if running_in_container():
        return (
            "/bin/bash -lc 'container=$(docker ps --format \"{{.Names}}\" | "
            "grep -E \"^(douyin-web-hostfix|douyin-web)$\" | head -n 1); "
            "[ -n \"$container\" ] && docker exec \"$container\" sh -lc "
            "\"cd /app && python main.py --doTask\"'"
        )
    if compose_file_path():
        return f"cd {compose_root()} && /usr/bin/docker compose run --rm task"
    return f"cd {repo_root()} && {shlex.quote(sys.executable)} main.py --doTask"


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


def run_task_now():
    try:
        log_file = Path(get_app_settings().get("ops_log_file") or "/var/log/douyin-sparkflow.log")
        command, cwd = build_task_run_spec()
        return run_background_command(
            command,
            log_file,
            cwd=cwd,
            env={
                "SPARKFLOW_MANUAL_RUN": "1",
                "PYTHONUNBUFFERED": "1",
            },
        )
    except Exception as exc:
        import traceback
        Path("task_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logger.error("run_task_now failed: %s", exc)
        return -1


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
        "daily_schedule": current_daily_schedule(),
        "crontab": read_crontab(),
        "log_tail": read_log_tail(120),
        "image_present": _check_image_present(),
    }
