#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiProxy 管理服务

Python3 标准库实现，无第三方依赖，随 fnOS 应用一起分发。
提供管理页静态资源与 REST API：

    GET  /api/status                  内核/管理服务/配置/订阅缓存状态
    GET  /api/settings                读取当前配置（敏感字段脱敏）
    POST /api/settings                保存配置 -> 重新生成 config.yaml -> 重启内核
    GET  /api/subscriptions           订阅清单 + 当前启用项
    POST /api/subscriptions           保存清单；启用项变化时重新生成配置并重启内核
    GET  /api/logs?name=info&lines=N   读取日志尾部（白名单）
    POST /api/refresh                 立即刷新订阅（刷新并应用）

    GET  /api/kernel                  内核版本 / 内置副本 / 备份 / 任务状态
    POST /api/kernel/check            检查 mihomo 官方新版本（拉 GitHub Releases）
    POST /api/kernel/install          下载并切换内核（github / file / bundled）
    GET  /api/kernel/task             当前内核任务进度（前端轮询）
    POST /api/kernel/rollback         回滚到某个备份
    POST /api/kernel/config           保存下载镜像前缀

鉴权：若 settings.conf 中设置了 wizard_secret，则所有 /api/* 需要
      Authorization: Bearer <secret> 或 X-Admin-Token: <secret>。

运行（由 cmd/main 拉起）：
    TRIM_PKGVAR=... TRIM_PKGETC=... TRIM_APPDEST=... ADMIN_PORT=9091 \
        python3 admin_server.py
"""

import gzip
import hmac
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

# ---------------------------------------------------------------- 路径与常量

CMD_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DEST = os.environ.get("TRIM_APPDEST") or os.path.dirname(CMD_DIR)
VAR_DIR = os.environ.get("TRIM_PKGVAR") or os.path.join(APP_DEST, "var")
ETC_DIR = os.environ.get("TRIM_PKGETC") or os.path.join(APP_DEST, "etc")

# 生命周期脚本（config_callback / refresh_sub / main）所在的目录候选。
# ⚠️ 绝不能只认 __file__ 的兄弟目录：fnOS 安装/升级时会先在
# /vol1/appcenter-downloads/<包名> 里解包，并从那里执行 install_callback，
# 它再拉起 main、main 再拉起本服务，于是 __file__ 指向的就是那个临时目录。
# 装完临时目录被系统清理，进程却还活着（代码已在内存、静态页在数据目录），
# 此时所有 os.path.isfile(__file__ 兄弟) 都会失败——现象是「页面一切正常，
# 一保存就报找不到 config_callback 脚本」，直到手动重启应用才好。
# 所以这里按「真实安装目录 → 数据目录 → __file__ → 常见布局」依次探测。
CMD_DIRS = [
    os.path.join(APP_DEST, "cmd"),
    os.path.join(VAR_DIR, "cmd"),
    CMD_DIR,
    "/var/apps/miproxy/cmd",
    "/var/packages/miproxy/cmd",
]


def find_cmd_script(name):
    """在候选目录里找一个 cmd 脚本，返回 (路径或 None, 已查找过的目录列表)。"""
    tried = []
    for raw in CMD_DIRS:
        if not raw:
            continue
        folder = os.path.abspath(raw)
        if folder in tried:
            continue
        tried.append(folder)
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path, tried
    return None, tried


def missing_script_error(name, tried):
    """脚本找不到时的统一报错：把查过哪些目录摊开，别让人靠猜。"""
    return {
        "ok": False,
        "searched": tried,
        "error": "找不到 %s 脚本（已查找：%s）。常见原因是应用刚安装/升级完，"
                 "管理服务仍从系统的临时解包目录运行、而该目录已被清理——"
                 "到 fnOS 应用中心把 MiProxy 重启一次即可恢复正常。"
                 % (name, "、".join(tried)),
    }


SETTINGS_FILE = os.path.join(ETC_DIR, "settings.conf")
CONFIG_FILE = os.path.join(VAR_DIR, "config.yaml")
PID_FILE = os.path.join(VAR_DIR, "admin.pid")
MIHOMO_PID_FILE = os.path.join(VAR_DIR, "app.pid")
ADMIN_LOG = os.path.join(VAR_DIR, "admin.log")
SUBSCRIPTION_FILE = os.path.join(VAR_DIR, "proxy_providers", "subscription.yaml")
# 订阅清单（可存多个，但同一时间只有一个启用）：启用项始终落盘为 settings.conf 的 wizard_sub_*
SUBS_FILE = os.path.join(ETC_DIR, "subscriptions.json")
# 配置应用的进度文件（由 cmd/config_callback 写，管理服务只读）
# 为什么用文件而不是内存：进度由「跑配置的脚本」产生，而脚本是独立进程，
# 两者没有共享内存；文件是两者之间最简单可靠的通道。
APPLY_PROGRESS_FILE = os.path.join(VAR_DIR, "apply.progress")
MAX_SUBS = 30
LEGACY_SUB_ID = "legacy"

UI_DIR_CANDIDATES = [
    os.path.join(VAR_DIR, "ui", "admin"),
    os.path.join(APP_DEST, "ui", "admin"),
]

# 只允许读取这些日志，避免任意文件读取
LOG_WHITELIST = {
    "info": "info.log",
    "refresh": "refresh_sub.log",
    "install": "install_callback.log",
    "config": "config_callback.log",
    "upgrade": "upgrade_callback.log",
    "admin": "admin.log",
}

MAX_LINES = 2000
MAX_BODY = 64 * 1024

PORT = 9091


# ---------------------------------------------------------------- 通用工具

def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def read_settings():
    """解析 settings.conf（key=value，一行一个）。"""
    data = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                data[key.strip()] = val.strip()
    except OSError:
        pass
    return data


def bash_bin():
    """定位 bash。fnOS 上是 /bin/bash；Windows 本地调试可用 MIPROXY_BASH 显式指定，
    否则 PATH 里的 bash 可能指向 WSL 启动器而非真正的 shell。"""
    forced = os.environ.get("MIPROXY_BASH", "").strip()
    if forced and os.path.isfile(forced):
        return forced
    for path in ("/bin/bash", "/usr/bin/bash"):
        if os.path.isfile(path):
            return path
    return shutil.which("bash") or "bash"


def auth_token():
    """管理接口令牌：优先复用面板密钥。"""
    settings = read_settings()
    return (settings.get("wizard_secret") or settings.get("wizard_admin_token") or "").strip()


def read_pid(path):
    try:
        with open(path, encoding="utf-8") as fh:
            parts = fh.read().strip().split()
        return int(parts[0]) if parts else None
    except (OSError, ValueError):
        return None


_PID_CACHE = {}
PID_CACHE_TTL = 2.0     # 秒：同一 pid 的结果短暂复用，避免界面轮询把 CPU 拖满


def _win_pid_alive(pid):
    """Windows 存活探测。

    不能用 os.kill(pid, 0)（那是 TerminateProcess，直接把进程杀掉），
    也尽量不用 tasklist（枚举全部进程，本机实测单次 3~6 秒，会让 /api/status 卡住）。
    这里用 OpenProcess + GetExitCodeProcess，毫秒级；拿不到句柄再退回 tasklist。
    """
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 非 Windows / ctypes 不可用时退回 tasklist
        pass
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, timeout=8)
        return re.search(rb"\b" + str(pid).encode() + rb"\b", out.stdout or b"") is not None
    except (OSError, subprocess.SubprocessError):
        return False


def pid_alive(pid):
    if not pid or pid <= 0:
        return False
    now = time.time()
    hit = _PID_CACHE.get(pid)
    if hit and now - hit[0] < PID_CACHE_TTL:
        return hit[1]
    if os.name == "nt":
        alive = _win_pid_alive(pid)
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if len(_PID_CACHE) > 64:
        _PID_CACHE.clear()
    _PID_CACHE[pid] = (now, alive)
    return alive


def tail_lines(path, lines):
    """高效读取文件尾部若干行（不整体载入大日志）。"""
    lines = max(1, min(to_int(lines, 200), MAX_LINES))
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        chunk = 8192
        data = b""
        pos = size
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
        return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:])


def safe_tail(path, lines):
    try:
        if not os.path.isfile(path):
            return ""
        return tail_lines(path, lines)
    except Exception as exc:  # noqa: BLE001 - 日志读取失败不应影响接口
        return "[读取日志失败] %s" % exc


def _last_interesting_log_line(keywords, path=None):
    """从 info.log 尾部找日志行，按 keywords 的优先级依次尝试。

    用来把脚本自己的诊断原话带回接口错误信息（例如「订阅返回的是 base64
    编码内容…」），而不是在服务端再猜一遍格式。keywords 有优先级：第一个
    关键词命中不了才看下一个——诊断行（为什么坏）比处置行（做了什么）更重要。
    """
    target = path or os.path.join(VAR_DIR, "info.log")
    try:
        if not os.path.isfile(target):
            return ""
        with open(target, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    tail = lines[-200:]
    for kw in keywords:
        for line in reversed(tail):
            if kw in line:
                # 去掉时间戳前缀与装饰符号，只留正文
                parts = line.split(" - ", 1)
                text = parts[1].strip() if len(parts) > 1 else line.strip()
                return text.lstrip("❌⚠️✅ ").strip()
    return ""


# ---------------------------------------------------------------- 配置读写

def public_settings():
    """给前端的配置视图，密码/密钥只返回是否已设置。"""
    s = read_settings()
    return {
        "sub_type": s.get("wizard_sub_type", "none") or "none",
        "sub_url": s.get("wizard_sub_url", ""),
        "sub_path": s.get("wizard_sub_path", ""),
        "refresh_interval": to_int(s.get("wizard_refresh_interval"), 0),
        "http_port": to_int(s.get("wizard_http_port"), 7890),
        "api_port": to_int(s.get("wizard_api_port"), 9090),
        "proxy_user": s.get("wizard_proxy_user", ""),
        "proxy_pass_set": bool(s.get("wizard_proxy_pass")),
        "secret_set": bool(s.get("wizard_secret")),
        "tun_enable": s.get("wizard_tun_enable", "false") == "true",
    }


def current_sub():
    """settings.conf 里当前启用的订阅（运行时唯一真相源）。"""
    s = read_settings()
    return {
        "type": s.get("wizard_sub_type", "none") or "none",
        "url": s.get("wizard_sub_url", ""),
        "path": s.get("wizard_sub_path", ""),
    }


def same_sub(item, cur):
    if item.get("type") != cur.get("type"):
        return False
    if cur.get("type") == "remote":
        return bool(cur.get("url")) and item.get("url") == cur.get("url")
    if cur.get("type") == "local":
        return bool(cur.get("path")) and item.get("path") == cur.get("path")
    return False


def read_subs_file():
    """读取订阅清单（缺失或损坏时返回空清单，不抛异常）。"""
    data = None
    try:
        with open(SUBS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    items = data.get("items") if isinstance(data, dict) else None
    clean = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("id") or "").strip()
        if not sid:
            continue
        clean.append({
            "id": sid,
            "name": str(it.get("name") or "")[:60],
            "type": "local" if str(it.get("type")) == "local" else "remote",
            "url": str(it.get("url") or "").strip(),
            "path": str(it.get("path") or "").strip(),
        })
    active = str(data.get("active_id") or "").strip() if isinstance(data, dict) else ""
    return {"items": clean[:MAX_SUBS], "active_id": active}


def write_subs_file(data):
    """原子写：先写临时文件再替换，避免断电/并发留下半截 JSON。"""
    tmp = SUBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, SUBS_FILE)
    try:
        os.chmod(SUBS_FILE, 0o666)
    except OSError:
        pass


def effective_subs():
    """清单 + 当前生效项。以 settings.conf 为准反查 active，兼顾向导里直接改过订阅的情况。"""
    data = read_subs_file()
    cur = current_sub()
    active = ""
    if cur["type"] in ("remote", "local"):
        for it in data["items"]:
            if same_sub(it, cur):
                active = it["id"]
                break
        if not active:
            # 向导里配置的订阅不在清单中：临时合成一条，方便用户“认领”为正式条目
            data["items"].insert(0, {
                "id": LEGACY_SUB_ID,
                "name": "当前配置（来自安装/配置向导）",
                "type": cur["type"],
                "url": cur["url"],
                "path": cur["path"],
            })
            active = LEGACY_SUB_ID
    data["active_id"] = active
    data["current"] = cur
    return data


def normalize_sub_items(raw_items):
    """校验并规范化订阅列表；错误信息给到前端逐条展示。"""
    errors = []
    items = []
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        return [], ["订阅列表格式错误（应为数组）"]
    if len(raw_items) > MAX_SUBS:
        return [], ["订阅数量过多（最多 %d 个）" % MAX_SUBS]

    used_ids = set()
    for idx, raw in enumerate(raw_items, 1):
        if not isinstance(raw, dict):
            errors.append("第 %d 项格式错误" % idx)
            continue
        sid = str(raw.get("id") or "").strip()
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", sid) or sid in used_ids:
            sid = "s" + uuid.uuid4().hex[:8]
        used_ids.add(sid)

        name = str(raw.get("name") or "").strip()[:60] or ("订阅 %d" % idx)
        stype = "local" if str(raw.get("type") or "") == "local" else "remote"
        url = str(raw.get("url") or "").strip()
        path = str(raw.get("path") or "").strip()

        if stype == "remote":
            if not re.match(r"^https?://", url):
                errors.append("「%s」的订阅 URL 必须以 http:// 或 https:// 开头" % name)
            if len(url) > 2000:
                errors.append("「%s」的订阅 URL 过长（最多 2000 字符）" % name)
            path = ""
        else:
            if not path:
                errors.append("「%s」需填写本地订阅文件路径" % name)
            elif len(path) > 500:
                errors.append("「%s」的文件路径过长（最多 500 字符）" % name)
            url = ""

        items.append({"id": sid, "name": name, "type": stype, "url": url, "path": path})
    return items, errors


def validate_settings(data):
    errors = []
    sub_type = data.get("sub_type", "none")
    if sub_type not in ("none", "remote", "local"):
        errors.append("订阅类型必须是 none / remote / local")

    if sub_type == "remote":
        url = str(data.get("sub_url") or "").strip()
        if not re.match(r"^https?://", url):
            errors.append("远程订阅 URL 必须以 http:// 或 https:// 开头")
    if sub_type == "local":
        if not str(data.get("sub_path") or "").strip():
            errors.append("本地订阅需填写文件路径")

    interval = to_int(data.get("refresh_interval"), -1)
    if interval < 0 or interval > 168:
        errors.append("刷新间隔需为 0-168 的整数（小时）")

    ports = {}
    for key, label in (("http_port", "代理端口"), ("api_port", "面板端口")):
        port = to_int(data.get(key), -1)
        if port < 1 or port > 65535:
            errors.append("%s需为 1-65535 的整数" % label)
        ports[key] = port
    if ports.get("http_port") == ports.get("api_port"):
        errors.append("代理端口与面板端口不能相同")

    return errors


# 配置变更与订阅刷新都要跑 config_callback（会重生成 config.yaml 并重启内核），
# 并发执行会互相覆盖配置、把内核反复重启。这里用一把非阻塞锁串行化，
# 并与内核任务互斥（内核升级期间不允许改配置，反之亦然）。
_APPLY_LOCK = threading.Lock()

# ---------------------------------------------------------------- 配置应用任务
#
# 为什么改成「异步任务 + 轮询进度」：
# 应用一次配置要跑十几秒到一分钟（下载订阅 → 解析 → 生成配置 → 重启内核）。
# 过去是同步等到底才返回，前端只能显示一个转圈的按钮，用户既不知道进行到哪一步，
# 也没法知道到底卡住了还是只是慢。
# 现在：POST 立刻返回「已受理」，后台线程干活，进度写到 apply.progress，
# 前端轮询 GET /api/apply/progress 就能看到「正在下载订阅信息 → 解析中 →
# 生成配置中 → 重启内核中 → 已完成」，并用遮罩挡住其它操作。
#
# 状态字段：state=running|done|error|unknown，stage/percent/message 描述当前阶段。
APPLY_TASK = {
    "state": "unknown", "stage": "", "percent": 0, "message": "",
    "started_at": 0, "finished_at": 0, "error": "", "result": {},
}
APPLY_TASK_LOCK = threading.Lock()

# 进度阶段 → 展示文案。键与 config_callback 里 progress_set 的 stage 对应。
APPLY_STAGE_TEXT = {
    "start": "准备中",
    "downloading": "正在下载订阅信息",
    "parsing": "正在解析订阅内容",
    "generating": "正在生成配置文件",
    "saving": "正在保存配置",
    "restarting": "正在重启内核",
    "done": "已完成",
    "failed": "执行失败",
}


def _read_apply_progress():
    """读 config_callback 写下的进度文件。"""
    stage, percent, message = "", 0, ""
    try:
        with open(APPLY_PROGRESS_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key == "stage":
                    stage = val
                elif key == "percent":
                    percent = to_int(val, 0)
                elif key == "message":
                    message = val
    except OSError:
        return "", 0, ""
    return stage, percent, message


def apply_progress_snapshot():
    """给前端的进度快照。"""
    with APPLY_TASK_LOCK:
        snap = dict(APPLY_TASK)
    # 进度文件是「活的」，任务状态是「记账的」，两者合起来才是完整视图
    stage, percent, message = _read_apply_progress()
    if stage:
        snap["stage"] = stage
        snap["percent"] = percent
        snap["message"] = message or APPLY_STAGE_TEXT.get(stage, stage)
    snap["stage_text"] = APPLY_STAGE_TEXT.get(snap.get("stage", ""), snap.get("message", ""))
    snap["running"] = snap.get("state") == "running"
    return snap


def apply_task_running():
    with APPLY_TASK_LOCK:
        return APPLY_TASK.get("state") == "running"


def _apply_task_update(**kw):
    with APPLY_TASK_LOCK:
        APPLY_TASK.update(kw)


def _apply_task_reset():
    with APPLY_TASK_LOCK:
        APPLY_TASK.update({
            "state": "running", "stage": "start", "percent": 0, "message": "准备中",
            "started_at": int(time.time()), "finished_at": 0, "error": "", "result": {},
        })


def apply_settings(data):
    """写入配置并重启内核。通过 config_callback 复用既有的配置生成逻辑。

    返回 202（已受理，异步执行）；最终结果通过 /api/apply/progress 取。
    """
    script, tried = find_cmd_script("config_callback")
    if not script:
        return 500, missing_script_error("config_callback", tried)
    if kernel_task_running():
        return 409, {"ok": False, "error": "内核任务（升级/切换/回滚）正在进行，请稍候再修改配置"}
    if not _APPLY_LOCK.acquire(blocking=False):
        return 409, {"ok": False, "error": "上一次配置变更还在执行，请稍候再试"}
    try:
        # 注意：锁由后台线程释放（见 _apply_settings_locked 里的 _worker），
        # 这里不能在 finally 里释放，否则并发保护形同虚设。
        return _apply_settings_locked(data, script)
    except Exception:
        _APPLY_LOCK.release()
        raise


def _config_sig():
    """config.yaml 的 (mtime_ns, size)，用来判断这次应用到底有没有真的重写配置。"""
    try:
        st = os.stat(CONFIG_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def _apply_settings_locked(data, script):
    env = os.environ.copy()
    # 关键：重启内核时不要连带杀掉管理服务自身
    env["MIPROXY_KEEP_ADMIN"] = "1"

    # 敏感字段：显式“清除”优先；否则留空表示沿用旧值，避免误抹密钥
    current = read_settings()
    proxy_pass = str(data.get("proxy_pass") or "")
    if data.get("clear_proxy_pass"):
        proxy_pass = ""
    elif not proxy_pass:
        proxy_pass = current.get("wizard_proxy_pass", "")
    secret = str(data.get("secret") or "")
    if data.get("clear_secret"):
        secret = ""
    elif not secret:
        secret = current.get("wizard_secret", "")

    env["wizard_sub_type"] = str(data.get("sub_type", "none"))
    env["wizard_sub_url"] = str(data.get("sub_url") or "")
    env["wizard_sub_path"] = str(data.get("sub_path") or "")
    env["wizard_refresh_interval"] = str(to_int(data.get("refresh_interval"), 0))
    env["wizard_http_port"] = str(to_int(data.get("http_port"), 7890))
    env["wizard_api_port"] = str(to_int(data.get("api_port"), 9090))
    env["wizard_proxy_user"] = str(data.get("proxy_user") or "")
    env["wizard_proxy_pass"] = proxy_pass
    env["wizard_secret"] = secret
    env["wizard_tun_enable"] = "true" if data.get("tun_enable") else "false"

    cfg_before = _config_sig()
    pid_before = read_pid(MIHOMO_PID_FILE)

    # 后台执行：接口立刻返回，前端轮询 /api/apply/progress 看阶段。
    # 为什么必须异步：应用一次配置要跑十几秒到一分钟，同步等待期间用户
    # 既看不到「进行到哪一步」，也没法判断是卡住了还是只是慢。
    def _worker():
        try:
            code, body = _run_config_callback(env, script, cfg_before, pid_before)
        except Exception as exc:  # noqa: BLE001
            code, body = 500, {"ok": False, "error": "执行配置应用时发生异常: %s" % exc}
        finally:
            _APPLY_LOCK.release()
        _apply_task_update(
            state=("done" if code == 200 else "error"),
            stage=("done" if code == 200 else "failed"),
            percent=100,
            message=(body.get("error") or "已完成") if code != 200 else "已完成",
            error=(body.get("error") or "") if code != 200 else "",
            finished_at=int(time.time()),
            result={"code": code, "body": body},
        )

    _apply_task_reset()
    threading.Thread(target=_worker, name="apply-config", daemon=True).start()

    # 202 Accepted：任务已受理，结果稍后通过 /api/apply/progress 取
    return 202, {
        "ok": True,
        "accepted": True,
        "running": True,
        "message": "配置应用已开始，进度见 /api/apply/progress",
    }


def _run_config_callback(env, script, cfg_before, pid_before):
    """真正执行 config_callback 并汇总结果（跑在后台线程里）。"""
    try:
        proc = subprocess.run(
            [bash_bin(), script], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        return 504, {"ok": False, "error": "应用配置超时（config_callback 未在 300 秒内完成）"}
    except OSError as exc:
        return 500, {"ok": False, "error": "执行 config_callback 失败: %s" % exc}

    cfg_after = _config_sig()
    pid_after = read_pid(MIHOMO_PID_FILE)
    running = pid_alive(pid_after)

    # 三个独立的事实，前端据此判断「到底生效了没有」：
    #   config_updated   —— config.yaml 被重写过
    #   kernel_restarted —— 内核换了新 PID（旧进程还在跑旧配置的情况必须暴露出来）
    config_updated = cfg_after[0] != 0 and cfg_after != cfg_before
    kernel_restarted = bool(pid_after) and pid_after != pid_before
    ok = proc.returncode == 0

    body = {
        "ok": ok,
        "returncode": proc.returncode,
        "config_updated": config_updated,
        "kernel_restarted": kernel_restarted,
        "kernel_pid_before": pid_before,
        "kernel_pid_after": pid_after,
        "mihomo_running": running,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
        "log": safe_tail(os.path.join(VAR_DIR, "info.log"), 40),
    }
    if ok:
        return 200, body

    # 非 0 就别再返回 200：前端（以及任何直接调 API 的脚本）都该明确看到这是失败。
    # 返回码 3 是 config_callback 约定的「配置已写入但内核重启失败」，
    # 返回码 4 是「订阅没解析出任何节点」（节点没生效，且可能保留了旧配置）。
    # 返回码 5 是「另一个配置应用正在进行」（跨进程锁没拿到）。
    # 返回码 6 是「新订阅下载失败，本次切换已整体回滚」（原订阅与配置原样保留）。
    if proc.returncode == 3:
        body["error"] = "配置已写入，但内核重启失败——新配置不会生效，请查看日志"
    elif proc.returncode == 4:
        # 把 config_callback 的诊断原话带上来：它已经写清了是 base64 / HTML / 缺字段
        diag = _last_interesting_log_line(("订阅未解析出任何节点：", "已保留原 config.yaml"))
        if diag.startswith("订阅未解析出任何节点："):
            diag = diag[len("订阅未解析出任何节点："):].strip()
        body["error"] = "订阅未解析出任何节点，节点不会生效%s" % (("：" + diag) if diag else "，请检查订阅链接返回的是否为 Clash/mihomo 格式")
        body["sub_nodes"] = 0
    elif proc.returncode == 5:
        body["error"] = "另一个配置应用正在进行，请稍后重试"
    elif proc.returncode == 6:
        body["error"] = "新订阅下载失败，本次切换未生效：已保留原订阅与当前配置，可稍后重试或检查订阅链接"
        diag = _last_interesting_log_line(("订阅下载失败", "不是 Clash/mihomo 格式"))
        if diag:
            body["error"] += "（" + diag + "）"
    else:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        body["error"] = "生成配置失败（config_callback 返回 %s）%s" % (
            proc.returncode, ("：" + detail[-1]) if detail else "",
        )
    return 500, body


def do_refresh():
    """立即执行一次订阅刷新（复用 refresh_sub 的 once 模式）。"""
    script, tried = find_cmd_script("refresh_sub")
    if not script:
        return 500, missing_script_error("refresh_sub", tried)
    if kernel_task_running():
        return 409, {"ok": False, "error": "内核任务（升级/切换/回滚）正在进行，请稍候再刷新订阅"}
    if not _APPLY_LOCK.acquire(blocking=False):
        return 409, {"ok": False, "error": "上一次配置变更/刷新还在执行，请稍候再试"}
    try:
        return _do_refresh_locked(script)
    finally:
        _APPLY_LOCK.release()


def _do_refresh_locked(script):

    env = os.environ.copy()
    env["MIPROXY_KEEP_ADMIN"] = "1"
    try:
        proc = subprocess.run(
            [bash_bin(), script, "once"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150,
        )
    except subprocess.TimeoutExpired:
        return 504, {"ok": False, "error": "刷新订阅超时"}
    except OSError as exc:
        return 500, {"ok": False, "error": "执行 refresh_sub 失败: %s" % exc}

    return 200, {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "log": safe_tail(os.path.join(VAR_DIR, "refresh_sub.log"), 60),
    }


# ---------------------------------------------------------------- 节点列表与延迟测试
#
# 节点清单来自 config.yaml 的 proxies 段（也就是「实际生效」的那份，不是订阅缓存），
# 实时状态与延迟尽量走 mihomo 的 external-controller —— 那才是内核眼里的真相。

NODE_TEST_URL_DEFAULT = "http://www.gstatic.com/generate_204"
NODE_TEST_TIMEOUT_MS = 4000
MAX_NODE_TESTS = 300
BUILTIN_PROXIES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
# mihomo 的 /proxies 里既有真正的节点，也有策略组和内置出口。
# 只有前两者之外的才是「节点」，统计时必须剔除，否则数字会虚高。
PROXY_GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay", "smart"}
BUILTIN_PROXY_TYPES = {"direct", "reject", "rejectdrop", "pass", "compatible"}


def _is_real_proxy(name, item):
    """内核 /proxies 里的一个条目是不是真正的节点（而非策略组或内置出口）。"""
    if name in BUILTIN_PROXIES:
        return False
    kind = str((item or {}).get("type") or "").strip().lower()
    if kind in BUILTIN_PROXY_TYPES or kind in PROXY_GROUP_TYPES:
        return False
    return True


def _api_port():
    try:
        return int((read_settings().get("wizard_api_port") or "9090").strip() or 9090)
    except (TypeError, ValueError):
        return 9090


def _mihomo_secret():
    """调用内核控制端口用的密钥：以 config.yaml 里的 secret 为准。

    设置项 wizard_secret 是「期望值」，config.yaml 的 secret 才是「内核实际生效值」。
    用户在 fnOS 里改了面板密钥、但没重新应用配置时，两者会不一致；此时按设置项
    去请求必然 401，节点页会整页显示 unauthorized，看起来像内核坏了。
    所以优先读内核真正在用的那份，读不到再退回设置项。
    """
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("secret:"):
                    value = line[len("secret:"):].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    return value
    except OSError:
        pass
    return (read_settings().get("wizard_secret") or "").strip()


def _mihomo_api(path, timeout=4.0):
    """调用 mihomo external-controller，失败返回 None（内核没起、密钥不对、超时都算失败）。

    注意：显式禁用环境变量里的 http_proxy —— 这是本机回环地址，
    走代理只会绕一圈然后失败。
    """
    url = "http://127.0.0.1:%d%s" % (_api_port(), path)
    req = urllib.request.Request(url)
    secret = _mihomo_secret()
    if secret:
        req.add_header("Authorization", "Bearer " + secret)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # 测速超时是 408，body 里带 {"message": "..."}，这是有效信息不能丢
        try:
            return json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, OSError):
            return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _yaml_scalar(text):
    value = (text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _split_flow_map(body):
    """把 {name: "a", type: ss, server: 1.2.3.4, port: 443} 拆成 [(k, v)]。
    逗号只在引号外才算分隔符，避免 name 里带逗号被切坏。"""
    parts, buf, quote_ch = [], [], None
    for ch in body:
        if quote_ch:
            buf.append(ch)
            if ch == quote_ch:
                quote_ch = None
        elif ch in "\"'":
            quote_ch = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    pairs = []
    for part in parts:
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        if key.strip():
            pairs.append((key.strip(), _yaml_scalar(val)))
    return pairs


def parse_config_proxies():
    """从 config.yaml 的 proxies 段读出节点基础信息（name/type/server/port）。

    只实现够用的 YAML 子集，不引第三方依赖，两种常见写法都支持：
        - {name: "香港01", type: ss, server: hk.example.com, port: 443}
        - name: "香港01"
          type: ss
          server: hk.example.com
          port: 443
    """
    try:
        with open(CONFIG_FILE, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []

    nodes, cur, in_block = [], None, False
    for raw in lines:
        if not in_block:
            if raw.strip() == "proxies:":
                in_block = True
            continue
        # 行首顶格的键意味着 proxies 段结束（rules: / proxy-groups: ...）
        # ⚠️ 但顶格的 "- xxx" 是列表项、不是段结束 —— YAML 允许序列与父键同缩进，
        #    机场订阅（subconverter 之类转换出来的）大量用这种写法。
        #    早前这里一见顶格就 break：订阅解析出 80 多个节点、内核也正常加载，
        #    管理界面却一个节点都显示不出来（订阅明明是好的，看着却像没生效）。
        if raw and not raw[0].isspace() and not raw.startswith("-"):
            break
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            body = line[1:].strip()
            cur = {}
            nodes.append(cur)
            if body.startswith("{"):
                for key, val in _split_flow_map(body.strip().strip("{}")):
                    cur[key] = val
            elif ":" in body:
                key, val = body.split(":", 1)
                cur[key.strip()] = _yaml_scalar(val)
            # 「- 」单独一行、字段全在下一层缩进里：cur 已经是 nodes 里的那个对象，
            # 不要再重新赋值（否则后续字段写进了一个游离的字典，节点会变成空壳）。
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            if key and key not in cur:
                cur[key] = _yaml_scalar(val)

    out = []
    for idx, node in enumerate(nodes, 1):
        name = str(node.get("name") or "").strip()
        if not name or name in BUILTIN_PROXIES:
            continue
        out.append({
            "index": idx,
            "name": name,
            "type": str(node.get("type") or "").strip(),
            "server": str(node.get("server") or "").strip(),
            "port": str(node.get("port") or "").strip(),
            "network": str(node.get("network") or "").strip(),
            "udp": str(node.get("udp") or "").strip().lower() in ("true", "yes", "1"),
        })
    return out


def build_nodes():
    """节点清单 + 内核侧实时延迟（history 最后一条）。"""
    nodes = parse_config_proxies()
    kernel_alive = pid_alive(read_pid(MIHOMO_PID_FILE))

    live = {}
    kernel_nodes = 0
    if kernel_alive:
        data = _mihomo_api("/proxies")
        for name, item in ((data or {}).get("proxies") or {}).items():
            if not isinstance(item, dict):
                continue
            if _is_real_proxy(name, item):
                kernel_nodes += 1
            delay = None
            hist = item.get("history")
            if isinstance(hist, list) and hist:
                try:
                    last = hist[-1]
                    if isinstance(last, dict) and last.get("delay"):
                        delay = int(last["delay"])
                except (TypeError, ValueError, AttributeError):
                    delay = None
            live[name] = {"delay": delay, "alive": bool(item.get("alive")), "type": item.get("type") or ""}

    for node in nodes:
        info = live.get(node["name"])
        node["delay"] = info["delay"] if info else None
        node["alive"] = info["alive"] if info else None
        if info and not node["type"]:
            node["type"] = info["type"]

    sub = effective_subs()
    sub_name = ""
    for item in sub["items"]:
        if item["id"] == sub["active_id"]:
            sub_name = item.get("name") or ""
            break

    return {
        "ok": True,
        "kernel_running": kernel_alive,
        "subscription": sub_name,
        "sub_type": sub.get("current", {}).get("type", "none"),
        "total": len(nodes),
        # config_nodes：config.yaml 里真实写进去的节点数
        # kernel_nodes：内核实际加载的节点数
        # 两个数不一致就说明「配置写了但内核没吃进去」，管理界面据此给出准确提示，
        # 而不是笼统地说一句「没有节点」。
        "config_nodes": len(nodes),
        "kernel_nodes": kernel_nodes,
        "nodes": nodes,
        "test_url": NODE_TEST_URL_DEFAULT,
    }


def test_nodes(names, url=None, timeout_ms=NODE_TEST_TIMEOUT_MS):
    """通过内核实际发请求测延迟（mihomo 的 /proxies/{name}/delay）。"""
    if not pid_alive(read_pid(MIHOMO_PID_FILE)):
        return 409, {"ok": False, "error": "内核未运行，无法测速——请先在「概览」页启动内核"}

    clean, seen = [], set()
    for item in names or []:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            clean.append(name)
    clean = clean[:MAX_NODE_TESTS]
    if not clean:
        return 400, {"ok": False, "error": "没有需要测试的节点"}

    url = (url or NODE_TEST_URL_DEFAULT).strip()
    if not re.match(r"^https?://", url) or len(url) > 200:
        return 400, {"ok": False, "error": "测试地址需以 http:// 或 https:// 开头且不超过 200 字符"}

    try:
        timeout_ms = max(500, min(15000, int(timeout_ms)))
    except (TypeError, ValueError):
        timeout_ms = NODE_TEST_TIMEOUT_MS

    def one(name):
        query = "/proxies/%s/delay?url=%s&timeout=%d" % (
            quote(name, safe=""), quote(url, safe=""), timeout_ms,
        )
        data = _mihomo_api(query, timeout=timeout_ms / 1000.0 + 4)
        if not isinstance(data, dict):
            return name, {"delay": 0, "error": "内核无响应"}
        try:
            delay = int(data.get("delay") or 0)
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            return name, {"delay": delay, "error": ""}
        message = str(data.get("message") or "").strip()
        return name, {"delay": 0, "error": message or "超时"}

    results = {}
    # 并发 6：再高会把内核打满并互相拖慢，全量测速反而更慢
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, result in pool.map(one, clean):
            results[name] = result
    return 200, {"ok": True, "url": url, "timeout": timeout_ms, "results": results}


# ---------------------------------------------------------------- 内核管理（mihomo 检查更新 / 切换 / 升级）

KERNEL_DIR = os.path.join(VAR_DIR, "target")
KERNEL_BIN = os.path.join(KERNEL_DIR, "mihomo")
KERNEL_BUNDLED = os.path.join(KERNEL_DIR, "mihomo.bundled")
KERNEL_BACKUP_DIR = os.path.join(VAR_DIR, "kernel_backup")
KERNEL_STATE_FILE = os.path.join(ETC_DIR, "kernel.json")

# 官方仓库地址（本地调试/自建镜像可用环境变量覆盖，属于运维开关，不接受前端传参）
KERNEL_API_BASE = (os.environ.get("MIPROXY_KERNEL_API")
                   or "https://api.github.com/repos/MetaCubeX/mihomo").rstrip("/")
KERNEL_DL_BASE = (os.environ.get("MIPROXY_KERNEL_BASE")
                  or "https://github.com/MetaCubeX/mihomo/releases/download").rstrip("/")
MAX_BACKUPS = 3
MAX_RELEASES = 20

KERNEL_VER_RE = re.compile(r"(v\d+\.\d+\.\d+|alpha-[0-9a-fA-F]+)")
VERSION_TAG_RE = re.compile(r"^v?\d{1,3}\.\d{1,3}\.\d{1,3}(-[0-9A-Za-z.-]+)?$")

# uname -m -> mihomo release 资产里的架构名
ARCH_MAP = {
    "x86_64": "amd64", "amd64": "amd64",
    "aarch64": "arm64", "arm64": "arm64", "armv8l": "arm64",
    "armv7l": "armv7", "armv7": "armv7",
    "armv6l": "armv6", "armv6": "armv6",
    "i386": "386", "i486": "386", "i586": "386", "i686": "386", "x86": "386",
    "riscv64": "riscv64", "loongarch64": "loong64", "s390x": "s390x",
    "mips": "mips-softfloat", "mips64": "mips64-softfloat",
    "mipsel": "mipsle-softfloat", "mips64el": "mips64le-softfloat",
}

KERNEL_TASK = {
    "id": "",
    "state": "idle",        # idle | running | done | error
    "phase": "",
    "percent": 0,
    "message": "",
    "error": "",
    "started_at": 0,
    "finished_at": 0,
    "result": {},
    "log": [],
}
KERNEL_TASK_LOCK = threading.Lock()
_VER_CACHE = {}


class KernelError(Exception):
    """内核操作失败（消息直接展示给用户）。"""


def read_kernel_state():
    state = {"mirror": "", "releases": [], "latest": None, "checked_at": 0}
    try:
        with open(KERNEL_STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            state.update({
                "mirror": str(data.get("mirror") or "").strip(),
                "releases": data.get("releases") if isinstance(data.get("releases"), list) else [],
                "latest": data.get("latest") if isinstance(data.get("latest"), dict) else None,
                "checked_at": to_int(data.get("checked_at"), 0),
            })
    except (OSError, ValueError):
        pass
    return state


def write_kernel_state(state):
    os.makedirs(ETC_DIR, exist_ok=True)
    tmp = KERNEL_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, KERNEL_STATE_FILE)


def detect_arch():
    """返回 (machine, mihomo 资产架构名)。"""
    machine = (platform.machine() or "").strip().lower()
    return machine, ARCH_MAP.get(machine, machine or "amd64")


def _run_kernel(binary, args, timeout=20):
    """执行内核二进制。

    Windows 本地调试时内核二进制是 ELF（或测试桩脚本），直接 exec 会 WinError 193，
    改由 bash 启动；fnOS 上始终是直接执行。

    ELF 交给 bash 会把几十 MB 的二进制当脚本逐行解析，能把 -v 拖到超时（进而卡住
    /api/status），所以在 Windows 上先看一眼文件头，是 ELF 就直接判失败。
    """
    if os.name == "nt":
        try:
            with open(binary, "rb") as fh:
                magic = fh.read(4)
        except OSError:
            magic = b""
        if magic == b"\x7fELF":
            raise OSError("当前平台无法执行 Linux ELF 内核（本地调试环境下属正常）")
        cmd = [bash_bin(), binary] + list(args)
    else:
        cmd = [binary] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def kernel_version_of(path):
    """调用 `<bin> -v` 解析版本；结果按 (size, mtime) 缓存，避免每次轮询都起进程。"""
    if not os.path.isfile(path):
        return {"exists": False, "version": "", "raw": "", "size": 0, "mtime": 0}
    try:
        st = os.stat(path)
    except OSError as exc:
        return {"exists": True, "version": "", "raw": "", "size": 0, "mtime": 0, "error": str(exc)}
    info = {"exists": True, "size": st.st_size, "mtime": int(st.st_mtime), "raw": "", "version": ""}
    key = (st.st_size, int(st.st_mtime))
    cached = _VER_CACHE.get(path)
    if cached and cached[0] == key:
        info.update(cached[1])
        return info
    try:
        proc = _run_kernel(path, ["-v"], timeout=15)
        text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        first = text.splitlines()[0].strip() if text else ""
    except subprocess.TimeoutExpired:
        first, info["error"] = "", "执行 -v 超时"
    except OSError as exc:
        first, info["error"] = "", "无法执行: %s" % exc
    info["raw"] = first
    match = KERNEL_VER_RE.search(first)
    info["version"] = match.group(1) if match else ""
    _VER_CACHE[path] = (key, {"raw": info["raw"], "version": info["version"]})
    return info


def list_kernel_backups():
    items = []
    try:
        names = os.listdir(KERNEL_BACKUP_DIR)
    except OSError:
        return items
    for name in names:
        if name.startswith(".") or name.endswith((".tmp", ".new")):
            continue
        path = os.path.join(KERNEL_BACKUP_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        match = KERNEL_VER_RE.search(name)
        items.append({
            "name": name,
            "version": match.group(1) if match else "",
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        })
    items.sort(key=lambda x: (x["mtime"], x["name"]), reverse=True)
    return items


def _prune_backups():
    items = list_kernel_backups()
    for old in items[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(KERNEL_BACKUP_DIR, old["name"]))
        except OSError:
            pass


def _backup_current():
    """备份当前内核，返回备份条目（当前不存在则返回 None）。"""
    if not os.path.isfile(KERNEL_BIN):
        return None
    os.makedirs(KERNEL_BACKUP_DIR, exist_ok=True)
    info = kernel_version_of(KERNEL_BIN)
    label = re.sub(r"[^0-9A-Za-z._-]", "_", info.get("version") or "unknown")
    name = "mihomo-%s-%s" % (label, time.strftime("%Y%m%d-%H%M%S"))
    dst = os.path.join(KERNEL_BACKUP_DIR, name)
    shutil.copy2(KERNEL_BIN, dst)
    try:
        os.chmod(dst, 0o755)
    except OSError:
        pass
    _prune_backups()
    return {"name": name, "version": info.get("version") or "", "size": os.path.getsize(dst),
            "mtime": int(os.path.getmtime(dst))}


def _task_reset_locked(task_id):
    """占位成 running（调用方必须已持有 KERNEL_TASK_LOCK，否则会死锁）。"""
    KERNEL_TASK.update({
        "id": task_id, "state": "running", "phase": "准备", "percent": 1,
        "message": "", "error": "", "started_at": int(time.time()),
        "finished_at": 0, "result": {}, "log": [],
    })


def _task_reset(task_id):
    with KERNEL_TASK_LOCK:
        _task_reset_locked(task_id)


def _task_fail(message):
    """把任务标记为失败（用于「占位后才发现参数不合法」的路径，避免卡在 running）。"""
    _task_update(state="error", phase="失败", error=message, message=message,
                 finished_at=int(time.time()))


def kernel_task_running():
    with KERNEL_TASK_LOCK:
        return KERNEL_TASK.get("state") == "running"


def _task_update(**kw):
    with KERNEL_TASK_LOCK:
        KERNEL_TASK.update(kw)


def _task_log(line):
    stamp = time.strftime("%H:%M:%S")
    with KERNEL_TASK_LOCK:
        log = KERNEL_TASK.get("log") or []
        log.append("%s %s" % (stamp, line))
        KERNEL_TASK["log"] = log[-40:]


def kernel_task_snapshot():
    with KERNEL_TASK_LOCK:
        snap = dict(KERNEL_TASK)
        snap["log"] = list(KERNEL_TASK.get("log") or [])
        snap["result"] = dict(KERNEL_TASK.get("result") or {})
    return snap


# 镜像前缀只对官方 GitHub 地址生效：镜像服务（如 ghfast.top）认得的是原始 GitHub URL，
# 给自建/本地地址套前缀只会拼出无效地址。
MIRROR_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                "codeload.github.com", "raw.githubusercontent.com")


def _apply_mirror(url, mirror):
    """镜像前缀式加速：镜像 + 原始地址（如 https://ghfast.top/https://github.com/...）。"""
    mirror = (mirror or "").strip()
    if not mirror:
        return url
    host = (urlparse(url).netloc or "").split("@")[-1].split(":")[0].lower()
    if host not in MIRROR_HOSTS:
        return url
    if not mirror.endswith("/"):
        mirror += "/"
    return mirror + url


def _open_url(url, timeout=20, mirror=""):
    target = _apply_mirror(url, mirror)
    req = urllib.request.Request(target, headers={
        "User-Agent": "MiProxyAdmin/1.0 (+fnOS)",
        "Accept": "application/vnd.github+json, application/octet-stream",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def _http_json(url, timeout=15, mirror=""):
    with _open_url(url, timeout, mirror) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _fetch_releases(mirror):
    """拉取 releases；先试镜像（国内更快），再直连。返回 (list, error)。"""
    url = KERNEL_API_BASE + "/releases?per_page=%d" % MAX_RELEASES
    tried, errors = [], []
    for base in ([_apply_mirror(url, mirror), url] if mirror else [url]):
        if base in tried:
            continue
        tried.append(base)
        try:
            data = _http_json(base, 10)
            if isinstance(data, list):
                return data, ""
            errors.append("%s -> 返回不是列表" % base)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            errors.append("%s -> %s" % (base, exc))
    return [], "；".join(errors[-2:])


def _pick_asset(assets, asset_arch, tag):
    """在当前架构的资产里挑一个：优先精确匹配 mihomo-linux-<arch>-<tag>(.gz)。"""
    exact = ["mihomo-linux-%s-%s.gz" % (asset_arch, tag), "mihomo-linux-%s-%s" % (asset_arch, tag)]
    for want in exact:
        for item in assets:
            if (item.get("name") or "") == want:
                return item
    for item in assets:
        name = item.get("name") or ""
        if name.startswith("mihomo-linux-%s-" % asset_arch) and (name.endswith(".gz") or "." not in name[-6:]):
            return item
    return None


def _normalize_releases(raw, asset_arch):
    out = []
    for rel in raw:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = str(rel.get("tag_name") or "").strip()
        if not tag:
            continue
        asset = _pick_asset(rel.get("assets") or [], asset_arch, tag)
        out.append({
            "tag": tag,
            "published_at": str(rel.get("published_at") or ""),
            "prerelease": bool(rel.get("prerelease")),
            "asset": (asset or {}).get("name") or "",
            "size": to_int((asset or {}).get("size"), 0),
            "url": (asset or {}).get("browser_download_url") or "",
        })
    return out


def check_kernel_updates(force=True):
    """检查更新：拉取 Releases → 缓存到 kernel.json。"""
    state = read_kernel_state()
    machine, asset_arch = detect_arch()
    if not force and state.get("releases"):
        return 200, {
            "ok": True,
            "cached": True,
            "mirror": state["mirror"],
            "arch": {"machine": machine, "asset": asset_arch},
            "latest": state.get("latest"),
            "releases": state["releases"],
            "checked_at": state.get("checked_at"),
        }

    raw, error = _fetch_releases(state["mirror"])
    if not raw:
        return 200, {
            "ok": False,
            "cached": bool(state.get("releases")),
            "error": "无法访问 GitHub Releases：%s" % (error or "未知错误"),
            "hint": "可在本页「下载源」填写镜像前缀（如 https://ghfast.top/），或直接在下方按版本号安装",
            "mirror": state["mirror"],
            "arch": {"machine": machine, "asset": asset_arch},
            "latest": state.get("latest"),
            "releases": state.get("releases") or [],
            "checked_at": state.get("checked_at"),
        }

    releases = _normalize_releases(raw, asset_arch)
    latest = next((r for r in releases if not r["prerelease"] and r["asset"]), None)
    if latest is None:
        latest = next((r for r in releases if r["asset"]), None)

    state.update({"releases": releases, "latest": latest, "checked_at": int(time.time())})
    try:
        write_kernel_state(state)
    except OSError as exc:
        error = "写入 kernel.json 失败: %s" % exc

    current = kernel_version_of(KERNEL_BIN)
    return 200, {
        "ok": True,
        "cached": False,
        "warning": error or "",
        "mirror": state["mirror"],
        "arch": {"machine": machine, "asset": asset_arch},
        "current": current.get("version") or "",
        "latest": latest,
        "releases": releases,
        "checked_at": state["checked_at"],
        "update_available": bool(latest and current.get("version")
                                 and latest["tag"] != current["version"]),
    }


def kernel_overview():
    state = read_kernel_state()
    machine, asset_arch = detect_arch()
    current = kernel_version_of(KERNEL_BIN)
    bundled = kernel_version_of(KERNEL_BUNDLED)
    latest = state.get("latest")
    update_available = bool(
        latest and latest.get("tag") and current.get("version")
        and latest["tag"] != current["version"]
    )
    return {
        "current": current,
        "bundled": bundled,
        "arch": {"machine": machine, "asset": asset_arch},
        "mirror": state.get("mirror") or "",
        "latest": latest,
        "releases": state.get("releases") or [],
        "checked_at": state.get("checked_at") or 0,
        "update_available": update_available,
        "backups": list_kernel_backups(),
        "max_backups": MAX_BACKUPS,
        "task": kernel_task_snapshot(),
        "api_base": KERNEL_API_BASE,
        "download_base": KERNEL_DL_BASE,
    }


def _download_kernel(url, dest, mirror, label):
    """下载到 dest（.gz 自动解压），期间更新任务进度。"""
    suffix = ".gz" if url.lower().endswith(".gz") else ""
    tmp = dest + ".part"
    try:
        with _open_url(url, timeout=30, mirror=mirror) as resp:
            total = to_int(resp.headers.get("Content-Length"), 0)
            _task_update(message="正在下载 %s%s" % (label, ("（%.1f MB）" % (total / 1048576.0)) if total else ""))
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(131072)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    pct = 5 + (done / total * 62 if total else 0)
                    _task_update(percent=round(min(pct, 67), 1), message="已下载 %.1f MB%s"
                                 % (done / 1048576.0, (" / %.1f MB" % (total / 1048576.0)) if total else ""))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise KernelError("下载失败：%s（可在本页填写镜像前缀后重试）" % exc)

    if suffix:
        _task_update(phase="解压", percent=70, message="解压 .gz 内核包")
        try:
            with gzip.open(tmp, "rb") as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
        except (OSError, EOFError) as exc:
            raise KernelError("解压失败（文件可能下载不完整）：%s" % exc)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    else:
        os.replace(tmp, dest)
    return dest


def _restart_kernel():
    """重启内核（保留管理服务）。返回 (ok, 日志尾部)。

    存活判定统一走 `main status`：pid 文件的语义由 main 自己维护
    （Linux 上是真实 pid，本地 Windows 调试时是 MSYS pid），不要在这里另算。
    """
    main_script, tried = find_cmd_script("main")
    if not main_script:
        raise KernelError("找不到 cmd/main，无法重启内核（已查找：%s）" % "、".join(tried))
    env = os.environ.copy()
    env["MIPROXY_KEEP_ADMIN"] = "1"
    try:
        proc = subprocess.run([bash_bin(), main_script, "restart"], env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return False, "重启超时（main restart 未在 300 秒内返回）"
    ok = proc.returncode == 0
    detail = ""
    if ok:
        try:
            st = subprocess.run([bash_bin(), main_script, "status"], env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if st.returncode != 0:
                ok = False
                detail = "main status 返回 %s（内核未在运行）" % st.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, detail = False, "校验内核状态失败：%s" % exc
    else:
        detail = "main restart 返回 %s" % proc.returncode
    tail = safe_tail(os.path.join(VAR_DIR, "info.log"), 15)
    return ok, (detail + "\n" + (tail or "")).strip() or ((proc.stderr or "")[-600:])


def _install_binary(src, origin, apply_now=True):
    """把 src 换成正式内核：校验 → 备份 → 原子替换 → 重启，失败自动回滚。"""
    _task_update(phase="校验", percent=74, message="校验新内核可执行")
    staged = KERNEL_BIN + ".new"
    try:
        shutil.copyfile(src, staged)
        os.chmod(staged, 0o755)
    except OSError as exc:
        raise KernelError("写入临时文件失败：%s" % exc)

    info = kernel_version_of(staged)
    if not info.get("version"):
        try:
            os.remove(staged)
        except OSError:
            pass
        raise KernelError("新内核无法运行或版本识别失败：%s"
                          % (info.get("raw") or info.get("error") or "（无输出）"))

    _task_update(phase="备份", percent=80, message="备份当前内核")
    backup = _backup_current()
    if backup:
        _task_log("已备份当前内核 → %s" % backup["name"])

    _task_update(phase="替换", percent=84, message="替换内核文件")
    try:
        os.replace(staged, KERNEL_BIN)
        os.chmod(KERNEL_BIN, 0o755)
    except OSError as exc:
        raise KernelError("替换内核文件失败：%s" % exc)
    _VER_CACHE.pop(KERNEL_BIN, None)

    if not apply_now:
        _task_update(state="done", phase="完成", percent=100,
                     message="已切换内核文件（未重启）", finished_at=int(time.time()))
        return {"from": (backup or {}).get("version", ""), "to": info["version"], "restarted": False}

    _task_update(phase="重启内核", percent=88, message="重启内核并等待就绪…")
    ok, detail = _restart_kernel()
    _task_log("重启结果：%s" % ("成功" if ok else "失败"))
    if ok:
        _task_update(state="done", phase="完成", percent=100, message="已切换到 %s，内核已重启" % info["version"],
                     finished_at=int(time.time()),
                     result={"from": (backup or {}).get("version", ""), "to": info["version"],
                             "restarted": True, "origin": origin, "backup": (backup or {}).get("name", "")})
        return {"from": (backup or {}).get("version", ""), "to": info["version"], "restarted": True}

    # 新内核起不来：回滚到备份，避免把用户留在「内核挂了」的状态
    _task_update(phase="回滚", percent=92, message="新内核启动失败，正在回滚…")
    _task_log("新内核启动失败，开始回滚：%s" % (detail or "")[:300])
    rolled = False
    if backup:
        try:
            shutil.copy2(os.path.join(KERNEL_BACKUP_DIR, backup["name"]), KERNEL_BIN)
            os.chmod(KERNEL_BIN, 0o755)
            _VER_CACHE.pop(KERNEL_BIN, None)
            rolled, roll_detail = _restart_kernel()
            _task_log("回滚%s" % ("成功" if rolled else "后仍启动失败：" + (roll_detail or "")[:200]))
        except OSError as exc:
            _task_log("回滚失败：%s" % exc)
    raise KernelError("新内核 %s 启动失败，%s。%s"
                      % (info["version"], "已回滚到 %s" % ((backup or {}).get("version") or "上一个备份")
                         if rolled else "回滚未成功，请手动处理",
                         "详见 info.log"))


def _resolve_target(req):
    """把前端请求解析成 (本地文件路径或下载信息)。"""
    source = str(req.get("source") or "github").strip().lower()
    if source == "file":
        path = str(req.get("path") or "").strip()
        if not path or not os.path.isfile(path):
            raise KernelError("本地文件不存在：%s" % (path or "（未填写路径）"))
        return {"kind": "file", "path": path, "label": os.path.basename(path),
                "version": kernel_version_of(path).get("version") or ""}
    if source == "bundled":
        if not os.path.isfile(KERNEL_BUNDLED):
            raise KernelError("安装包内置内核不存在（%s）" % KERNEL_BUNDLED)
        return {"kind": "file", "path": KERNEL_BUNDLED, "label": "内置内核 mihomo.bundled",
                "version": kernel_version_of(KERNEL_BUNDLED).get("version") or ""}

    tag = str(req.get("version") or "").strip()
    state = read_kernel_state()
    if not tag or tag.lower() == "latest":
        latest = state.get("latest") or {}
        tag = str(latest.get("tag") or "").strip()
        if not tag:
            code, payload = check_kernel_updates(force=True)
            latest = (payload or {}).get("latest") or {}
            tag = str(latest.get("tag") or "").strip()
        if not tag:
            raise KernelError("还没拿到可用的版本信息，请先「检查更新」，或直接填写版本号（如 v1.19.2）")
    if not VERSION_TAG_RE.match(tag):
        raise KernelError("版本号格式不合法：%s（示例：v1.19.2）" % tag)

    _, asset_arch = detect_arch()
    asset_name = str(req.get("asset") or "").strip()
    if asset_name and not re.match(r"^mihomo-linux-[0-9a-z-]+", asset_name):
        raise KernelError("资产名不合法：%s" % asset_name)

    url = ""
    for rel in state.get("releases") or []:
        if rel.get("tag") == tag and rel.get("url"):
            url = rel["url"]
            asset_name = asset_name or rel.get("asset") or ""
            break
    if not url:
        asset_name = asset_name or "mihomo-linux-%s-%s.gz" % (asset_arch, tag)
        url = "%s/%s/%s" % (KERNEL_DL_BASE, tag, asset_name)
    return {"kind": "remote", "url": url, "label": asset_name or tag, "version": tag,
            "mirror": state.get("mirror") or ""}


def _kernel_worker(req, target):
    task_id = KERNEL_TASK.get("id")
    staged = os.path.join(KERNEL_DIR, ".mihomo.download")
    try:
        os.makedirs(KERNEL_DIR, exist_ok=True)
        if target["kind"] == "file":
            _task_update(phase="读取", percent=40, message="从本地文件安装：%s" % target["path"])
            _task_log("来源：本地文件 %s" % target["path"])
            src = target["path"]
        else:
            _task_update(phase="下载", percent=5, message="准备下载 %s" % target["label"])
            _task_log("来源：%s" % target["url"])
            src = _download_kernel(target["url"], staged, target.get("mirror", ""), target["label"])
        result = _install_binary(src, req.get("source") or "github")
        with KERNEL_TASK_LOCK:
            KERNEL_TASK["result"] = dict(result, task_id=task_id)
    except KernelError as exc:
        _task_update(state="error", phase="失败", error=str(exc), message=str(exc),
                     finished_at=int(time.time()))
        _task_log("失败：%s" % exc)
    except Exception as exc:  # noqa: BLE001 - 后台线程必须兜底，否则任务会永远停在 running
        _task_update(state="error", phase="失败", error="内部错误：%s" % exc, message=str(exc),
                     finished_at=int(time.time()))
        _task_log("内部错误：%r" % exc)
    finally:
        for path in (staged, staged + ".part", KERNEL_BIN + ".new"):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass


def start_kernel_task(req):
    with KERNEL_TASK_LOCK:
        if KERNEL_TASK.get("state") == "running":
            return 409, {"ok": False, "error": "已有内核任务正在进行，请等待完成"}
        if _APPLY_LOCK.locked():
            return 409, {"ok": False, "error": "正在应用配置变更（config_callback 运行中），请稍候再操作内核"}
        task_id = uuid.uuid4().hex[:12]
        # 先在锁内占位：_resolve_target 可能触发网络请求（拉 Releases），
        # 期间若只是“检查过 running 就放行”，第二个请求会同时进入并并行装内核。
        _task_reset_locked(task_id)
    try:
        target = _resolve_target(req)   # 解析失败直接返回错误，不进入后台线程
    except KernelError as exc:
        _task_fail(str(exc))
        return 400, {"ok": False, "error": str(exc)}
    _task_log("目标：%s %s" % (target.get("version") or "?", target.get("label") or ""))
    thread = threading.Thread(target=_kernel_worker, args=(dict(req), target), daemon=True)
    thread.start()
    return 202, {"ok": True, "task_id": task_id, "task": kernel_task_snapshot()}


def start_kernel_rollback(req):
    with KERNEL_TASK_LOCK:
        if KERNEL_TASK.get("state") == "running":
            return 409, {"ok": False, "error": "已有内核任务正在进行，请等待完成"}
        if _APPLY_LOCK.locked():
            return 409, {"ok": False, "error": "正在应用配置变更（config_callback 运行中），请稍候再操作内核"}
        task_id = uuid.uuid4().hex[:12]
        _task_reset_locked(task_id)

    backups = list_kernel_backups()
    if not backups:
        _task_fail("还没有任何备份，无法回滚")
        return 400, {"ok": False, "error": "还没有任何备份，无法回滚"}
    name = str(req.get("name") or "").strip()
    target = next((b for b in backups if b["name"] == name), None) if name else backups[0]
    if target is None:
        _task_fail("备份不存在：%s" % name)
        return 400, {"ok": False, "error": "备份不存在：%s" % name}
    path = os.path.join(KERNEL_BACKUP_DIR, target["name"])
    if not os.path.isfile(path):
        _task_fail("备份文件已丢失：%s" % target["name"])
        return 400, {"ok": False, "error": "备份文件已丢失：%s" % target["name"]}

    _task_log("回滚到备份 %s" % target["name"])
    thread = threading.Thread(target=_kernel_worker, args=(
        {"source": "rollback"}, {"kind": "file", "path": path, "label": target["name"],
                                 "version": target["version"]}), daemon=True)
    thread.start()
    return 202, {"ok": True, "task_id": task_id, "task": kernel_task_snapshot()}


def save_kernel_mirror(data):
    mirror = str(data.get("mirror") or "").strip()
    if mirror and not re.match(r"^https?://", mirror):
        return 400, {"ok": False, "error": "镜像前缀需以 http:// 或 https:// 开头"}
    if len(mirror) > 300:
        return 400, {"ok": False, "error": "镜像前缀过长（最多 300 字符）"}
    state = read_kernel_state()
    state["mirror"] = mirror
    try:
        write_kernel_state(state)
    except OSError as exc:
        return 500, {"ok": False, "error": "写入 kernel.json 失败：%s" % exc}
    return 200, {"ok": True, "mirror": mirror}


# ---------------------------------------------------------------- 状态汇总

def build_status():
    mihomo_pid = read_pid(MIHOMO_PID_FILE)
    admin_pid = read_pid(PID_FILE)

    def file_info(path):
        try:
            st = os.stat(path)
            return {"exists": True, "size": st.st_size, "mtime": int(st.st_mtime)}
        except OSError:
            return {"exists": False}

    # mihomo 控制端口也就是「面板选择页」所在端口（external-ui 挂在 /ui），
    # 前端用它拼「返回面板」的地址，避免把 9090 写死在 HTML 里。
    try:
        panel_port = int((read_settings().get("wizard_api_port") or "9090").strip() or 9090)
    except (TypeError, ValueError):
        panel_port = 9090

    return {
        "mihomo": {"running": pid_alive(mihomo_pid), "pid": mihomo_pid},
        "admin": {"running": pid_alive(admin_pid), "pid": admin_pid, "port": PORT},
        "panel_port": panel_port,
        "config": file_info(CONFIG_FILE),
        "subscription": file_info(SUBSCRIPTION_FILE),
        "kernel": {"version": kernel_version_of(KERNEL_BIN).get("version") or "",
                   "size": file_info(KERNEL_BIN).get("size", 0)},
        "auth_required": bool(auth_token()),
        "write_locked": not auth_token(),
        "hostname": socket.gethostname(),
    }


# ---------------------------------------------------------------- HTTP 处理

class Handler(BaseHTTPRequestHandler):
    server_version = "MiProxyAdmin/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):
        try:
            with open(ADMIN_LOG, "a", encoding="utf-8") as fh:
                fh.write("%s %s\n" % (self.log_date_time_string(), fmt % args))
        except OSError:
            pass

    # -- 输出 ---------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _authorized(self):
        token = auth_token()
        if not token:
            return True
        got = self.headers.get("X-Admin-Token", "") or ""
        if not got:
            auth = self.headers.get("Authorization", "") or ""
            if auth.startswith("Bearer "):
                got = auth[7:]
        return hmac.compare_digest(got, token)

    def _write_blocked(self):
        """未设面板密钥时禁止一切写操作。

        服务监听 0.0.0.0，若没有密钥就等于“局域网内任何人可改配置”；
        而 /api/kernel/install 能下载/读取任意二进制并以 root 运行，
        这是实打实的未认证远程代码执行入口（镜像前缀也能被改成任意地址）。
        因此只读接口保持可用，POST 一律拒绝并提示去 fnOS 设置里填密钥。
        """
        if auth_token():
            return False
        self._json(403, {
            "ok": False,
            "need_token": True,
            "error": "出于安全考虑，未设置面板密钥时禁止修改配置。"
                     "请到 fnOS 应用中心 → MiProxy → 设置 → 「面板密钥」填写后保存，再回到本页输入同一密钥。",
        })
        return True

    # -- 静态资源 -----------------------------------------------
    def _serve_static(self, rel):
        base = next((d for d in UI_DIR_CANDIDATES if os.path.isdir(d)), None)
        if not base:
            return self._send(500, "管理页未部署（缺少 ui/admin）", "text/plain; charset=utf-8")

        rel = rel.split("?")[0].split("#")[0]
        base_abs = os.path.abspath(base)
        full = os.path.abspath(os.path.join(base_abs, rel))
        if full != base_abs and not full.startswith(base_abs + os.sep):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")

        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # -- GET ----------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/admin", "/admin/"):
            return self._serve_static("index.html")
        if path.startswith("/admin/"):
            return self._serve_static(path[len("/admin/"):])

        if path.startswith("/api/") and not self._authorized():
            return self._json(401, {"error": "未授权：请提供面板密钥"})

        if path == "/api/status":
            return self._json(200, build_status())

        if path == "/api/settings":
            return self._json(200, public_settings())

        if path == "/api/subscriptions":
            return self._json(200, effective_subs())

        if path == "/api/nodes":
            return self._json(200, build_nodes())

        if path == "/api/kernel":
            return self._json(200, kernel_overview())

        if path == "/api/kernel/task":
            return self._json(200, kernel_task_snapshot())

        if path == "/api/apply/progress":
            return self._json(200, apply_progress_snapshot())

        if path == "/api/logs":
            query = parse_qs(parsed.query)
            name = (query.get("name", ["info"])[0] or "info").strip()
            lines = query.get("lines", ["200"])[0]
            filename = LOG_WHITELIST.get(name)
            if not filename:
                return self._json(400, {"error": "未知日志: %s" % name})
            target = os.path.join(VAR_DIR, filename)
            if not os.path.isfile(target):
                return self._json(200, {"name": name, "content": "", "note": "日志文件尚不存在"})
            return self._json(200, {"name": name, "content": safe_tail(target, lines)})

        return self._json(404, {"error": "not found"})

    # -- POST ---------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._authorized():
            return self._json(401, {"error": "未授权：请提供面板密钥"})

        # 测速是只读操作（不写配置、不动内核进程），未设面板密钥时也放行：
        # 否则刚装好还没填密钥的用户连「节点通不通」都验证不了。
        if parsed.path == "/api/nodes/delay":
            length = to_int(self.headers.get("Content-Length", 0), 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "请求体为空或过大"})
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                return self._json(400, {"error": "JSON 解析失败: %s" % exc})
            if not isinstance(payload, dict):
                return self._json(400, {"error": "请求体必须是 JSON 对象"})
            code, resp = test_nodes(payload.get("names"), payload.get("url"), payload.get("timeout"))
            return self._json(code, resp)

        if self._write_blocked():
            return

        if parsed.path == "/api/settings":
            length = to_int(self.headers.get("Content-Length", 0), 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "请求体为空或过大"})
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                return self._json(400, {"error": "JSON 解析失败: %s" % exc})
            if not isinstance(data, dict):
                return self._json(400, {"error": "请求体必须是 JSON 对象"})
            # 订阅以清单为准：请求带了 active_sub_id 就按清单校准，避免前端旧状态把订阅覆盖掉
            if "active_sub_id" in data:
                subs = effective_subs()
                aid = str(data.get("active_sub_id") or "").strip()
                target = next((it for it in subs["items"] if it["id"] == aid), None)
                if target:
                    data["sub_type"] = target["type"]
                    data["sub_url"] = target["url"]
                    data["sub_path"] = target["path"]
                elif not aid:
                    data["sub_type"] = "none"
                    data["sub_url"] = ""
                    data["sub_path"] = ""
                else:
                    # 给了 id 但清单里找不到（例如另一个标签页刚删掉了这条）：
                    # 沿用当前实际生效的订阅，绝不在这里静默把订阅清成 none。
                    cur = current_sub()
                    data["sub_type"] = cur["type"]
                    data["sub_url"] = cur["url"]
                    data["sub_path"] = cur["path"]
            errors = validate_settings(data)
            if errors:
                return self._json(400, {"ok": False, "errors": errors})
            code, resp = apply_settings(data)
            return self._json(code, resp)

        if parsed.path == "/api/refresh":
            code, resp = do_refresh()
            return self._json(code, resp)

        if parsed.path in ("/api/kernel/check", "/api/kernel/install",
                           "/api/kernel/rollback", "/api/kernel/config"):
            data = {}
            length = to_int(self.headers.get("Content-Length", 0), 0)
            if length > 0:
                if length > MAX_BODY:
                    return self._json(400, {"error": "请求体过大"})
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    data = body if isinstance(body, dict) else {}
                except Exception as exc:  # noqa: BLE001
                    return self._json(400, {"error": "JSON 解析失败: %s" % exc})
            if parsed.path == "/api/kernel/check":
                code, resp = check_kernel_updates(force=True)
            elif parsed.path == "/api/kernel/config":
                code, resp = save_kernel_mirror(data)
            elif parsed.path == "/api/kernel/install":
                try:
                    code, resp = start_kernel_task(data)
                except KernelError as exc:
                    code, resp = 400, {"ok": False, "error": str(exc)}
            else:
                try:
                    code, resp = start_kernel_rollback(data)
                except KernelError as exc:
                    code, resp = 400, {"ok": False, "error": str(exc)}
            return self._json(code, resp)

        if parsed.path == "/api/subscriptions":
            length = to_int(self.headers.get("Content-Length", 0), 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "请求体为空或过大"})
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                return self._json(400, {"error": "JSON 解析失败: %s" % exc})
            if not isinstance(data, dict):
                return self._json(400, {"error": "请求体必须是 JSON 对象"})

            items, errors = normalize_sub_items(data.get("items"))
            active_id = str(data.get("active_id") or "").strip()
            if active_id and active_id not in set(it["id"] for it in items):
                errors.append("启用项不存在于订阅列表中")
            if errors:
                return self._json(400, {"ok": False, "errors": errors})

            write_subs_file({"items": items, "active_id": active_id})

            # 只有“启用项发生变化”才重生成配置并重启内核，纯增删改不打扰内核
            cur = current_sub()
            target = next((it for it in items if it["id"] == active_id), None)
            if target is None:
                changed = cur["type"] in ("remote", "local")
            else:
                changed = not same_sub(target, cur)
            if not changed:
                return self._json(200, {"ok": True, "applied": False, "active_id": active_id})

            payload = dict(public_settings())
            payload.update({
                "sub_type": target["type"] if target else "none",
                "sub_url": target["url"] if target else "",
                "sub_path": target["path"] if target else "",
                "proxy_pass": "",
                "secret": "",
                "clear_proxy_pass": False,
                "clear_secret": False,
            })
            code, resp = apply_settings(payload)
            resp.update({"applied": True, "active_id": active_id})
            return self._json(code, resp)
        return self._json(404, {"error": "not found"})


# ---------------------------------------------------------------- 入口

def main():
    global PORT
    PORT = to_int(os.environ.get("ADMIN_PORT") or read_settings().get("wizard_admin_port") or 9091, 9091)

    os.makedirs(VAR_DIR, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    def _shutdown(_signum, _frame):
        try:
            if read_pid(PID_FILE) == os.getpid():
                os.remove(PID_FILE)
        except OSError:
            pass
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    finally:
        try:
            if read_pid(PID_FILE) == os.getpid():
                os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
