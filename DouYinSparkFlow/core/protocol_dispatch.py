import asyncio
import json
import os
import random
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.msg_builder import build_messages_for_targets
from utils.config import get_userData, normalize_unique_id, repo_root, save_userData
from utils.logger import setup_logger


logger = setup_logger()
PROTOCOL_SCRIPT = repo_root() / "core" / "protocol_sender.mjs"
NODE_HELPER_IMAGE = "node:22-alpine"


def _coerce_non_negative_int(value, default):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _normalize_send_strategy(config):
    raw = config.get("sendStrategy", {}) or {}
    start_min = _coerce_non_negative_int(raw.get("accountStartDelaySecondsMin", 0), 0)
    start_max = _coerce_non_negative_int(raw.get("accountStartDelaySecondsMax", start_min), start_min)
    if start_max < start_min:
        start_max = start_min

    message_min = _coerce_non_negative_int(raw.get("messageIntervalSecondsMin", 0), 0)
    message_max = _coerce_non_negative_int(raw.get("messageIntervalSecondsMax", message_min), message_min)
    if message_max < message_min:
        message_max = message_min

    strategy = {
        "shuffleTargets": bool(raw.get("shuffleTargets", True)),
        "accountStartDelaySecondsMin": start_min,
        "accountStartDelaySecondsMax": start_max,
        "messageIntervalSecondsMin": message_min,
        "messageIntervalSecondsMax": message_max,
        "messageVariants": [str(item).strip() for item in raw.get("messageVariants", []) if str(item).strip()],
    }
    if os.getenv("SPARKFLOW_MANUAL_RUN") == "1":
        strategy["accountStartDelaySecondsMin"] = 0
        strategy["accountStartDelaySecondsMax"] = 0
        strategy["messageIntervalSecondsMin"] = min(strategy["messageIntervalSecondsMin"], 3)
        strategy["messageIntervalSecondsMax"] = min(strategy["messageIntervalSecondsMax"], 6)
    return strategy


def _account_identity_key(account):
    normalized_unique_id = normalize_unique_id(account.get("unique_id"))
    if normalized_unique_id:
        return f"uid:{normalized_unique_id}"

    username = str(account.get("username", "")).strip()
    if username:
        return f"user:{username}"

    return ""


def _merge_protocol_runtime_state(accounts, result_by_username):
    changed = False
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_accounts = get_userData(force_reload=True)
    accounts_by_identity = {
        identity: account
        for account in all_accounts
        for identity in [_account_identity_key(account)]
        if identity
    }

    for account in accounts:
        target_account = accounts_by_identity.get(_account_identity_key(account))
        if not target_account:
            continue

        result = result_by_username.get(account.get("username"))
        if not result:
            continue

        protocol_cache = result.get("protocol_targets_cache")
        if protocol_cache is not None:
            target_account["protocol_targets_cache"] = protocol_cache
            target_account["protocol_user_id"] = result.get("userId", "")
            changed = True

        history = dict(target_account.get("message_history") or {})
        for entry in result.get("sent", []):
            if entry.get("dryRun") or not entry.get("success", True):
                continue

            target = str(entry.get("target", "")).strip()
            message = str(entry.get("message", "")).strip()
            if not target or not message:
                continue

            history[target] = {
                "message": message,
                "sentAt": str(entry.get("sentAt", now_iso)),
            }
            changed = True

        if history:
            target_account["message_history"] = history

    if changed:
        save_userData(all_accounts)


def _host_repo_root():
    candidates = [
        Path("/opt/douyin-sparkflow/DouYinSparkFlow"),
        repo_root(),
    ]
    for candidate in candidates:
        if (candidate / "core" / "protocol_sender.mjs").exists():
            return candidate
    return repo_root()


def _build_protocol_command():
    node_path = shutil.which("node")
    if node_path:
        return [node_path, str(PROTOCOL_SCRIPT)], repo_root(), "local-node", str(repo_root())

    docker_path = shutil.which("docker")
    if docker_path:
        host_repo = _host_repo_root()
        return (
            [
                docker_path,
                "run",
                "--rm",
                "-i",
                "--network",
                "host",
                "-v",
                f"{host_repo}:/workspace",
                "-w",
                "/workspace",
                NODE_HELPER_IMAGE,
                "node",
                "core/protocol_sender.mjs",
            ],
            repo_root(),
            "docker-node-helper",
            "/workspace",
        )

    raise RuntimeError("Neither node nor docker is available for the protocol sender")


def _run_protocol_for_user(user, messages_by_target, dry_run, send_strategy):
    command, cwd, runner_label, runtime_repo_root = _build_protocol_command()
    payload = {
        "repoRoot": runtime_repo_root,
        "dryRun": dry_run,
        "account": user,
        "messagesByTarget": messages_by_target,
        "sendStrategy": send_strategy,
    }
    process = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(cwd),
        check=False,
    )

    stdout = (process.stdout or "").strip()
    if not stdout:
        raise RuntimeError(
            f"protocol sender returned no output for {user.get('username', 'unknown')}: {process.stderr}"
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"protocol sender produced invalid JSON for {user.get('username', 'unknown')}: {stdout}"
        ) from exc

    if process.returncode != 0 or not data.get("ok"):
        error_message = data.get("error") or process.stderr or "protocol sender failed"
        raise RuntimeError(
            f"{user.get('username', 'unknown')} protocol sender failed: {error_message}"
        )

    data["runner"] = runner_label

    return data


async def run_protocol_tasks(config, accounts, message_builder):
    del message_builder

    dry_run = bool(config.get("protocolDryRun", False))
    multi_task = bool(config.get("multiTask", True))
    concurrency = int(config.get("taskCount", 1)) if multi_task else 1
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    send_strategy = _normalize_send_strategy(config)

    async def _worker(user):
        async with semaphore:
            start_delay = random.randint(
                send_strategy["accountStartDelaySecondsMin"],
                send_strategy["accountStartDelaySecondsMax"],
            )
            if start_delay > 0:
                logger.info(
                    "Delaying protocol sender for %s by %ss to avoid synchronized bursts",
                    user.get("username", "unknown"),
                    start_delay,
                )
                await asyncio.sleep(start_delay)

            logger.info("Starting protocol sender for %s", user.get("username", "unknown"))
            messages_by_target = build_messages_for_targets(
                user.get("targets", []),
                previous_messages=user.get("message_history", {}),
                config=config,
            )
            logger.info(
                "Prepared %s protocol messages for %s with shuffleTargets=%s interval=%s-%ss manual_run=%s",
                len(messages_by_target),
                user.get("username", "unknown"),
                send_strategy["shuffleTargets"],
                send_strategy["messageIntervalSecondsMin"],
                send_strategy["messageIntervalSecondsMax"],
                os.getenv("SPARKFLOW_MANUAL_RUN") == "1",
            )
            result = await asyncio.to_thread(
                _run_protocol_for_user,
                user,
                messages_by_target,
                dry_run,
                send_strategy,
            )
            logger.info(
                "Protocol sender finished for %s resolved=%s unresolved=%s sent=%s",
                user.get("username", "unknown"),
                len(result.get("resolved", [])),
                len(result.get("unresolved", [])),
                len(result.get("sent", [])),
            )
            return result

    gathered = await asyncio.gather(*(_worker(user) for user in accounts), return_exceptions=True)

    result_by_username = {}
    failures = []
    for user, item in zip(accounts, gathered):
        if isinstance(item, Exception):
            failures.append(str(item))
            logger.error("Protocol sender failed for %s: %s", user.get("username", "unknown"), item)
            continue
        result_by_username[user.get("username")] = item
        unresolved = item.get("unresolved", [])
        if unresolved:
            logger.warning(
                "Protocol sender could not resolve %s targets for %s: %s",
                len(unresolved),
                user.get("username", "unknown"),
                [entry.get("target") for entry in unresolved],
            )

    _merge_protocol_runtime_state(accounts, result_by_username)

    if failures and not result_by_username:
        raise RuntimeError("; ".join(failures))

    return [result_by_username[user.get("username")] for user in accounts if user.get("username") in result_by_username]
