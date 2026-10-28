#!/usr/bin/env python3
"""MiProxy 订阅节点链路自检（可在开发机与 NAS 上直接跑）。

覆盖的是最容易被忽略、又最要命的一段链路：
    订阅 YAML → config_callback 提取/规范化 → config.yaml → admin_server 解析 → 节点列表

历史事故：机场订阅常用「顶格 - 」写法（YAML 允许序列与父键同缩进），
config_callback 原样抄进 config.yaml，管理服务却按「块内必须缩进」切段，
遇到顶格就认为 proxies 段结束 —— 订阅明明有 80 多个节点、
内核也正常加载，管理界面却显示 0 个，用户完全看不出问题在哪。

用法：
    python3 scripts/selftest_nodes.py
退出码 0 = 全部通过。
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_CALLBACK = os.path.join(ROOT, "cmd", "config_callback")
ADMIN_SERVER = os.path.join(ROOT, "cmd", "admin_server.py")

FAILED = []


def find_bash():
    """找一个「真的」bash。

    Windows 上 PATH 里通常先撞上 C:\\Windows\\System32\\bash.exe —— 那是 WSL 启动器，
    跑起来会被安全策略拦掉，而且报错完全看不出是 bash 的问题。
    所以这里显式排除 System32 / WindowsApps，优先用 Git Bash。
    """
    import glob
    candidates = []
    forced = os.environ.get("MIPROXY_TEST_BASH", "").strip()
    if forced:
        candidates.append(forced)
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    for pattern in (
        "C:/Program Files/Git/usr/bin/bash.exe",
        "C:/Program Files/Git/bin/bash.exe",
        "C:/Users/*/AppData/Local/Programs/Git/usr/bin/bash.exe",
        "E:/workBuddy/resources/vendor/PortableGit/usr/bin/bash.exe",
        "C:/Users/*/.workbuddy/binaries/PortableGit/versions/*/usr/bin/bash.exe",
    ):
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    for path in candidates:
        low = path.replace("\\", "/").lower()
        if "system32" in low or "windowsapps" in low:
            continue
        if os.path.isfile(path):
            return path
    return None


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print("  [%s] %s%s" % (mark, name, ("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)
    return ok


# ---------------------------------------------------------------- 订阅样本

def sub_top_level():
    """顶格「-」写法（subconverter 等转换器的常见输出）。"""
    return """proxies:
- name: "\u9999\u6e2f 01"
  type: ss
  server: hk01.example.com
  port: 443
  cipher: aes-256-gcm
  password: p1
- name: "\u9999\u6e2f 02"
  type: ss
  server: hk02.example.com
  port: 443
  cipher: aes-256-gcm
  password: p2
- name: "\u65e5\u672c 01"
  type: ss
  server: jp01.example.com
  port: 443
  cipher: aes-256-gcm
  password: p3
proxy-groups:
- name: PROXY
  type: select
  proxies:
  - "\u9999\u6e2f 01"
  - "\u9999\u6e2f 02"
rules:
- MATCH,PROXY
"""


def sub_indented():
    """标准缩进写法。"""
    return """proxies:
  - name: "\u7f8e\u56fd 01"
    type: ss
    server: us01.example.com
    port: 443
    cipher: aes-256-gcm
    password: p1
  - name: "\u7f8e\u56fd 02"
    type: ss
    server: us02.example.com
    port: 443
    cipher: aes-256-gcm
    password: p2
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - "\u7f8e\u56fd 01"
      - "\u7f8e\u56fd 02"
rules:
  - MATCH,PROXY
"""


def sub_flow():
    """单行 flow 写法（- {name: ..., type: ...}）。"""
    return """proxies:
- {name: "\u5fb7\u56fd 01", type: ss, server: de01.example.com, port: 443, cipher: aes-256-gcm, password: p}
- {name: "\u5fb7\u56fd 02", type: ss, server: de02.example.com, port: 443, cipher: aes-256-gcm, password: p}
proxy-groups:
- {name: PROXY, type: select, proxies: ["\u5fb7\u56fd 01", "\u5fb7\u56fd 02"]}
rules:
- MATCH,PROXY
"""


def sub_proxy_providers():
    """proxy-providers 型订阅：节点由 provider 运行时拉取，config.yaml 里没有内联节点。"""
    return """proxy-providers:
  provider1:
    type: http
    url: https://example.com/provider.yaml
    path: ./proxy_providers/provider1.yaml
    interval: 3600
proxy-groups:
- name: PROXY
  type: select
  use:
  - provider1
rules:
- MATCH,PROXY
"""


def sub_no_proxies():
    """订阅里没有 proxies 段（只有策略组）。"""
    return """proxy-groups:
- name: PROXY
  type: select
  proxies: [DIRECT]
rules:
- MATCH,PROXY
"""


def sub_base64():
    """base64 编码的 v2ray 链接列表（非 Clash 格式）。"""
    import base64
    raw = "vmess://eyJhZCI6IjEiLCJwb3J0Ijo0NDN9\nss://YWVzLTI1Ni1nY206cGFzc0AxLjIuMy40OjQ0Mw==\n"
    return base64.b64encode(raw.encode()).decode() + "\n"


# ---------------------------------------------------------------- 测试脚手架

def make_env(base, sub_text):
    """搭一套仿 NAS 的目录（var / etc），返回 (env, var_dir, etc_dir, sub_path)。"""
    var_dir = os.path.join(base, "var")
    etc_dir = os.path.join(base, "etc")
    os.makedirs(os.path.join(var_dir, "proxy_providers"), exist_ok=True)
    os.makedirs(etc_dir, exist_ok=True)

    sub_path = os.path.join(base, "subscription.yaml")
    with open(sub_path, "w", encoding="utf-8") as fh:
        fh.write(sub_text)

    with open(os.path.join(etc_dir, "settings.conf"), "w", encoding="utf-8") as fh:
        fh.write("wizard_http_port=7890\n")
        fh.write("wizard_api_port=9090\n")
        fh.write("wizard_tun_enable=false\n")
        fh.write("wizard_sub_type=local\n")
        fh.write("wizard_sub_path=%s\n" % sub_path.replace("\\", "/"))
        fh.write("wizard_refresh_interval=0\n")
        fh.write("wizard_secret=test-secret\n")

    env = dict(os.environ)
    env["TRIM_PKGVAR"] = var_dir.replace("\\", "/")
    env["TRIM_PKGETC"] = etc_dir.replace("\\", "/")
    env["TRIM_APPDEST"] = os.path.join(base, "app").replace("\\", "/")
    env["MIPROXY_NO_RESTART"] = "1"      # 不重启内核，测试只关心配置生成
    env["wizard_sub_type"] = "local"
    env["wizard_sub_path"] = sub_path.replace("\\", "/")
    env["wizard_http_port"] = "7890"
    env["wizard_api_port"] = "9090"
    env["wizard_tun_enable"] = "false"
    env["wizard_secret"] = "test-secret"
    return env, var_dir, etc_dir, sub_path


def run_callback(env):
    bash = find_bash()
    if not bash:
        raise RuntimeError("找不到可用的 bash（请用 MIPROXY_TEST_BASH 指定 Git Bash 路径）")
    proc = subprocess.run([bash, CONFIG_CALLBACK], env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc


def load_admin(var_dir):
    """按指定数据目录加载 admin_server 模块。"""
    os.environ["TRIM_PKGVAR"] = var_dir.replace("\\", "/")
    os.environ["TRIM_APPDEST"] = var_dir.replace("\\", "/")
    spec = importlib.util.spec_from_file_location("admin_server_probe", ADMIN_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def proxies_block_indented(config_text):
    """生成的 config.yaml 里，proxies 段的列表项是否已经缩进到父键下。"""
    lines = config_text.splitlines()
    inblk = False
    for raw in lines:
        if not inblk:
            if raw.strip() == "proxies:":
                inblk = True
            continue
        if not raw.strip():
            continue
        return raw[:1].isspace()
    return True      # 没有 proxies 段也算通过


def count_proxies_entries(text):
    """数一份订阅 YAML 里 proxies 段的条目数（顶格/缩进都认）。"""
    count = 0
    inblk = False
    for raw in text.splitlines():
        if not inblk:
            if raw.strip() == "proxies:":
                inblk = True
            continue
        if raw and not raw[0].isspace():
            if raw.startswith("-"):
                count += 1
            else:
                break
            continue
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            count += 1
    return count


# ---------------------------------------------------------------- 用例

def case_format(label, sub_text, expect_names):
    print("\n== 用例：%s ==" % label)
    base = tempfile.mkdtemp(prefix="miproxy_selftest_")
    try:
        env, var_dir, etc_dir, _ = make_env(base, sub_text)
        proc = run_callback(env)
        check("config_callback 退出码为 0", proc.returncode == 0,
              "returncode=%s\n%s" % (proc.returncode, proc.stderr[-800:]))

        config_path = os.path.join(var_dir, "config.yaml")
        if not check("config.yaml 已生成", os.path.isfile(config_path)):
            return
        with open(config_path, encoding="utf-8") as fh:
            config_text = fh.read()

        check("proxies 段已规范缩进", proxies_block_indented(config_text),
              "proxies 段里出现了顶格列表项")

        mod = load_admin(var_dir)
        nodes = mod.parse_config_proxies()
        names = [n["name"] for n in nodes]
        check("管理服务解析出 %d 个节点" % len(expect_names),
              len(nodes) == len(expect_names), "实际 %d 个：%s" % (len(nodes), names))
        check("节点名称正确", names == expect_names, "实际：%s" % names)
        if nodes:
            check("节点带类型/地址",
                  bool(nodes[0]["type"]) and bool(nodes[0]["server"]),
                  "第一个节点：%s" % nodes[0])
        # 生成结果里节点数必须与「订阅里实际的条目数」一致
        expect_count = count_proxies_entries(sub_text)
        check("节点数与订阅条目数一致", len(nodes) == expect_count,
              "订阅 %d 条 / 解析 %d 条" % (expect_count, len(nodes)))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def case_zero_nodes():
    print("\n== 用例：订阅里没有节点（应判失败并保留原配置）==")
    base = tempfile.mkdtemp(prefix="miproxy_selftest_")
    try:
        env, var_dir, etc_dir, _ = make_env(base, sub_top_level())
        proc = run_callback(env)
        check("首次应用成功", proc.returncode == 0,
              "returncode=%s\n%s" % (proc.returncode, proc.stderr[-500:]))
        config_path = os.path.join(var_dir, "config.yaml")
        with open(config_path, encoding="utf-8") as fh:
            before = fh.read()
        check("旧配置里有节点", before.count("name:") >= 3)

        # 换成一份没有 proxies 的订阅
        env2, var_dir2, _, _ = make_env(base, sub_no_proxies())
        # 让它沿用同一个 var 目录（保留上一份 config.yaml）
        env2["TRIM_PKGVAR"] = env["TRIM_PKGVAR"]
        proc2 = run_callback(env2)
        check("无节点订阅返回 4", proc2.returncode == 4,
              "returncode=%s" % proc2.returncode)
        with open(config_path, encoding="utf-8") as fh:
            after = fh.read()
        check("原 config.yaml 未被覆盖", after == before)
        check("日志说明了原因",
              "未解析出任何节点" in _read_log(var_dir), _read_log(var_dir)[-300:])
    finally:
        shutil.rmtree(base, ignore_errors=True)


def case_base64():
    print("\n== 用例：订阅返回 base64（应判失败并保留原配置）==")
    base = tempfile.mkdtemp(prefix="miproxy_selftest_")
    try:
        env, var_dir, etc_dir, _ = make_env(base, sub_top_level())
        proc = run_callback(env)
        check("首次应用成功", proc.returncode == 0,
              "returncode=%s" % proc.returncode)
        config_path = os.path.join(var_dir, "config.yaml")
        with open(config_path, encoding="utf-8") as fh:
            before = fh.read()

        env2, _, _, _ = make_env(base, sub_base64())
        env2["TRIM_PKGVAR"] = env["TRIM_PKGVAR"]
        proc2 = run_callback(env2)
        check("base64 订阅返回 4", proc2.returncode == 4,
              "returncode=%s" % proc2.returncode)
        with open(config_path, encoding="utf-8") as fh:
            after = fh.read()
        check("原 config.yaml 未被覆盖", after == before)
        log = _read_log(var_dir)
        check("日志指出了 base64", "base64" in log, log[-300:])
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _read_log(var_dir):
    path = os.path.join(var_dir, "info.log")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def case_install_path():
    """首次安装路径（install_callback 里另有一份 extract_yaml_block 副本）。"""
    print("\n== 用例：首次安装（install_callback 生成配置）==")
    base = tempfile.mkdtemp(prefix="miproxy_selftest_")
    try:
        env, var_dir, etc_dir, _ = make_env(base, sub_top_level())
        app_dir = os.path.join(base, "app")
        os.makedirs(app_dir, exist_ok=True)
        env["TRIM_APPDEST"] = app_dir.replace("\\", "/")

        # 装完会顺带拉起管理服务，用独立端口并在结束后回收，别留后台进程
        admin_port = "19099"
        with open(os.path.join(etc_dir, "settings.conf"), "a", encoding="utf-8") as fh:
            fh.write("wizard_admin_port=%s\n" % admin_port)

        bash = find_bash()
        proc = subprocess.run([bash, os.path.join(ROOT, "cmd", "install_callback")],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        check("install_callback 退出码为 0", proc.returncode == 0,
              "returncode=%s\n%s" % (proc.returncode, proc.stderr[-500:]))

        config_path = os.path.join(var_dir, "config.yaml")
        if not check("config.yaml 已生成", os.path.isfile(config_path)):
            return
        with open(config_path, encoding="utf-8") as fh:
            config_text = fh.read()
        check("proxies 段已规范缩进", proxies_block_indented(config_text))

        mod = load_admin(var_dir)
        nodes = mod.parse_config_proxies()
        check("管理服务解析出 3 个节点", len(nodes) == 3, "实际 %d 个" % len(nodes))
        check("节点名称正确",
              [n["name"] for n in nodes] == ["\u9999\u6e2f 01", "\u9999\u6e2f 02", "\u65e5\u672c 01"],
              str([n["name"] for n in nodes]))
    finally:
        kill_admin(var_dir)
        shutil.rmtree(base, ignore_errors=True)


def kill_admin(var_dir):
    """回收测试时 install_callback 拉起的管理服务。"""
    pid_file = os.path.join(var_dir, "admin.pid")
    if not os.path.isfile(pid_file):
        return
    try:
        with open(pid_file, encoding="utf-8") as fh:
            pid = int((fh.read().strip() or "0"))
        if pid > 0:
            os.kill(pid, 15)
    except (OSError, ValueError):
        pass


def case_proxy_providers():
    """proxy-providers 型订阅：没有内联节点，但**不能**被判成失败。"""
    print("\n== 用例：proxy-providers 型订阅（不应判失败）==")
    base = tempfile.mkdtemp(prefix="miproxy_selftest_")
    try:
        env, var_dir, etc_dir, _ = make_env(base, sub_proxy_providers())
        proc = run_callback(env)
        check("config_callback 退出码为 0", proc.returncode == 0,
              "returncode=%s\n%s" % (proc.returncode, proc.stderr[-500:]))
        config_path = os.path.join(var_dir, "config.yaml")
        if not check("config.yaml 已生成", os.path.isfile(config_path)):
            return
        with open(config_path, encoding="utf-8") as fh:
            config_text = fh.read()
        check("config.yaml 里保留了 proxy-providers 段",
              "proxy-providers:" in config_text and "provider1" in config_text)
        mod = load_admin(var_dir)
        check("内联节点数为 0（provider 型订阅本就没有内联节点）",
              len(mod.parse_config_proxies()) == 0)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    print("MiProxy 订阅节点链路自检")
    print("仓库：%s" % ROOT)
    case_format("顶格「-」写法", sub_top_level(),
                ["\u9999\u6e2f 01", "\u9999\u6e2f 02", "\u65e5\u672c 01"])
    case_format("标准缩进写法", sub_indented(),
                ["\u7f8e\u56fd 01", "\u7f8e\u56fd 02"])
    case_format("单行 flow 写法", sub_flow(),
                ["\u5fb7\u56fd 01", "\u5fb7\u56fd 02"])
    case_install_path()
    case_proxy_providers()
    case_zero_nodes()
    case_base64()

    print("\n==============================")
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
