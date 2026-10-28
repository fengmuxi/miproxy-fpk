#!/usr/bin/env bash
# MiProxy 本地开发辅助脚本（不打包，直接跑）
#
# 用法：
#   bash scripts/dev-local.sh init       # 造「假装机目录」+ 跑 install_callback 生成配置
#   bash scripts/dev-local.sh admin      # 前台启动管理服务（默认 9091）
#   bash scripts/dev-local.sh sync-ui    # 把 app/ui 同步到数据目录（改完前端后执行）
#   bash scripts/dev-local.sh status     # 查看内核 / 管理服务状态
#   bash scripts/dev-local.sh restart    # 重启内核（改配置后必须重启才生效）
#   bash scripts/dev-local.sh stop       # 停止内核与管理服务
#   bash scripts/dev-local.sh clean      # 删除开发目录
#
# 向导字段透传（写入 settings.conf）：
#   wizard_sub_type=remote wizard_sub_url="https://example.com/clash.yaml" \
#       wizard_refresh_interval=6 bash scripts/dev-local.sh init
#
# 环境变量：
#   MIPROXY_DEV_ROOT  开发目录，默认 /tmp/miproxy-dev
#   MIPROXY_ARCH      内核架构 x86_64 / arm64，默认按 uname -m 判断
#   ADMIN_PORT        管理端口，默认 9091
#
# 说明：mihomo 内核是 Linux ELF，Windows（Git Bash）上启动不了属正常；
#       本脚本的价值在于本地跑回调、生成/校验 config.yaml、调试管理界面。

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_ROOT="${MIPROXY_DEV_ROOT:-/tmp/miproxy-dev}"
APP="$DEV_ROOT/app"
VAR="$DEV_ROOT/var"
ETC="$DEV_ROOT/etc"
ADMIN_PORT="${ADMIN_PORT:-9091}"

# 与 fnOS 注入的三个路径变量对齐，后续所有 cmd/* 脚本都靠它们定位目录
export TRIM_APPDEST="$APP"
export TRIM_PKGVAR="$VAR"
export TRIM_PKGETC="$ETC"
export TRIM_TEMP_LOGFILE="$DEV_ROOT/tmp.log"

detect_arch() {
    if [ -n "${MIPROXY_ARCH:-}" ]; then
        echo "$MIPROXY_ARCH"
        return
    fi
    case "$(uname -m)" in
        x86_64|amd64) echo "x86_64" ;;
        aarch64|arm64|armv8*) echo "arm64" ;;
        *) echo "x86_64" ;;
    esac
}

need_init() {
    if [ ! -f "$ETC/settings.conf" ]; then
        echo "❌ 还没初始化，先跑：bash scripts/dev-local.sh init"
        exit 1
    fi
}

cmd_init() {
    local arch
    arch="$(detect_arch)"

    if [ ! -f "$REPO_ROOT/app/server/$arch/mihomo" ]; then
        echo "❌ 仓库里没有 app/server/$arch/mihomo（可用 MIPROXY_ARCH 指定架构）"
        exit 1
    fi

    echo "▶ 仓库：    $REPO_ROOT"
    echo "▶ 开发目录：$DEV_ROOT（内核架构 $arch）"

    mkdir -p "$APP" "$VAR" "$ETC"
    cp -rf "$REPO_ROOT/app/." "$APP"/

    # 仓库里两个架构的内核并存，装机只能有一个：按当前机器选一个放到 app/server/mihomo
    cp -f "$APP/server/$arch/mihomo" "$APP/server/mihomo"
    rm -rf "$APP/server/x86_64" "$APP/server/arm64"
    echo "▶ 安装目录已铺好：$APP"

    echo "▶ 执行 install_callback ..."
    bash "$REPO_ROOT/cmd/install_callback"

    echo
    echo "▶ 产物："
    echo "   $VAR/config.yaml           mihomo 配置"
    echo "   $ETC/settings.conf         配置项"
    echo "   $VAR/install_callback.log  安装日志"
    echo "   $VAR/info.log              内核启动日志"
    echo
    echo "下一步："
    echo "   bash scripts/dev-local.sh admin     # 打开管理界面 $ADMIN_PORT"
    echo "   bash scripts/dev-local.sh status    # 看状态"
}

cmd_admin() {
    need_init
    if (exec 3<>"/dev/tcp/127.0.0.1/${ADMIN_PORT}") 2>/dev/null; then
        echo "ℹ️  端口 ${ADMIN_PORT} 已在监听（install_callback 起的后台服务还在跑）"
        echo "    直接访问 http://127.0.0.1:${ADMIN_PORT}/admin/"
        echo "    想在终端看请求日志：先 bash scripts/dev-local.sh stop，再跑本命令"
        echo "    想换端口：ADMIN_PORT=9092 bash scripts/dev-local.sh admin"
        exit 0
    fi
    echo "▶ 管理界面：http://127.0.0.1:${ADMIN_PORT}/admin/（Ctrl-C 退出）"
    echo "▶ 数据目录：$VAR"
    ADMIN_PORT="$ADMIN_PORT" exec python3 "$REPO_ROOT/cmd/admin_server.py"
}

cmd_sync_ui() {
    need_init
    mkdir -p "$VAR/ui"
    cp -rf "$REPO_ROOT/app/ui/." "$VAR/ui"/
    echo "✅ 已同步 app/ui -> $VAR/ui（刷新浏览器即可，管理页无需重启服务）"
}

cmd_status() {
    need_init

    # main status 是静默的：0 = 运行中，3 = 未运行（供脚本判断，不输出文本）
    local rc kernel_state
    bash "$REPO_ROOT/cmd/main" status >/dev/null 2>&1
    rc=$?
    case "$rc" in
        0) kernel_state="运行中" ;;
        3) kernel_state="未运行" ;;
        *) kernel_state="状态未知（rc=$rc）" ;;
    esac

    local admin_state
    if (exec 3<>"/dev/tcp/127.0.0.1/${ADMIN_PORT}") 2>/dev/null; then
        admin_state="监听中 → http://127.0.0.1:${ADMIN_PORT}/admin/"
    else
        admin_state="未运行（bash scripts/dev-local.sh admin）"
    fi

    local bin_size="缺失" bundled="无" cfg_lines="缺失"
    [ -f "$VAR/target/mihomo" ] && bin_size="$(wc -c < "$VAR/target/mihomo" | tr -d ' ') bytes"
    [ -f "$VAR/target/mihomo.bundled" ] && bundled="有"
    [ -f "$VAR/config.yaml" ] && cfg_lines="$(wc -l < "$VAR/config.yaml" | tr -d ' ') 行"

    local sub_type sub_url
    sub_type="$(grep '^wizard_sub_type=' "$ETC/settings.conf" 2>/dev/null | cut -d= -f2- | tr -d '\r\n')"
    sub_url="$(grep '^wizard_sub_url=' "$ETC/settings.conf" 2>/dev/null | cut -d= -f2- | tr -d '\r\n')"

    echo "开发目录：$DEV_ROOT"
    echo "内核状态：$kernel_state（文件 $bin_size，内置副本 $bundled）"
    echo "管理服务：$admin_state"
    echo "配置文件：config.yaml $cfg_lines"
    echo "订阅配置：type=${sub_type:-none}  url=${sub_url:-（空）}"
    echo "日志：    $VAR/info.log  $VAR/install_callback.log"
}

cmd_clean() {
    case "$DEV_ROOT" in
        ""|"/"|"$HOME")
            echo "❌ 拒绝删除 $DEV_ROOT"
            exit 1
            ;;
    esac
    rm -rf "$DEV_ROOT"
    echo "✅ 已删除 $DEV_ROOT"
}

usage() {
    # 打印文件头部的注释块（跳过 shebang 与首行说明）
    awk 'NR>2 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "${BASH_SOURCE[0]}"
}

case "${1:-}" in
    init)     cmd_init ;;
    admin)    cmd_admin ;;
    sync-ui)  cmd_sync_ui ;;
    status)   cmd_status ;;
    restart)  need_init; bash "$REPO_ROOT/cmd/main" restart ;;
    stop)     need_init; bash "$REPO_ROOT/cmd/main" stop ;;
    clean)    cmd_clean ;;
    ""|-h|--help|help) usage ;;
    *)
        echo "❌ 未知命令：$1"
        echo
        usage
        exit 1
        ;;
esac
