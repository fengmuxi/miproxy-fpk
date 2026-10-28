#!/bin/bash
# 订阅下载统一入口：带 Clash 专用 User-Agent，并在「拿回来的不是 Clash YAML」时
# 换一个 UA 重试一次。
#
# 为什么必须带 UA：绝大多数机场/订阅服务会根据请求头 User-Agent 决定返回什么格式。
# 用 curl 的默认 UA（curl/8.x）去拿，拿回来常常是 **base64 编码的 v2ray 链接**，
# 而不是 YAML —— 表现就是「订阅下载成功、但 proxies 0 行、节点一个都没有」。
# 带上 clash.meta / ClashforWindows 这类 UA，服务端才会返回 Clash/mihomo YAML。
#
# 用法：download_subscription <url> <输出路径>
# 成功返回 0；失败返回非 0（不覆盖已有缓存）。

SUB_UA_PRIMARY="clash.meta"
SUB_UA_FALLBACK="ClashforWindows/0.20.39"

# 判断一个文件是不是 Clash/mihomo YAML：得有 proxies: 或 proxy-providers: 段
looks_like_clash_yaml() {
    local f="$1"
    [ -s "${f}" ] || return 1
    grep -qE '^(proxies|proxy-providers)[[:space:]]*:' "${f}" 2>/dev/null
}

# 打印文件开头的一小段内容（去掉换行，便于写进日志）
peek_head() {
    local f="$1" n="${2:-120}"
    head -c "${n}" "${f}" 2>/dev/null | tr -d '\r\n' | cut -c1-${n}
}

download_subscription() {
    local url="$1" out="$2"
    local tmp="${out}.tmp"

    rm -f "${tmp}"
    if ! curl -fsSL --connect-timeout 15 --max-time 60 \
            -A "${SUB_UA_PRIMARY}" -H "Accept: */*" \
            "${url}" -o "${tmp}" 2>>"${DEBUG_LOG:-/dev/null}"; then
        rm -f "${tmp}"
        return 1
    fi
    if [ ! -s "${tmp}" ]; then
        rm -f "${tmp}"
        return 1
    fi

    if looks_like_clash_yaml "${tmp}"; then
        mv -f "${tmp}" "${out}"
        return 0
    fi

    # 内容不是 Clash YAML：先看是不是 base64 / HTML 之类的其它格式，再换个 UA 重试
    log_msg "  ⚠️ 下载内容不像 Clash YAML（开头: $(peek_head "${tmp}" 80)），换 UA 重试一次"
    local saved="${tmp}.ua1"
    mv -f "${tmp}" "${saved}"

    rm -f "${tmp}"
    if curl -fsSL --connect-timeout 15 --max-time 60 \
            -A "${SUB_UA_FALLBACK}" -H "Accept: */*" \
            "${url}" -o "${tmp}" 2>>"${DEBUG_LOG:-/dev/null}" && [ -s "${tmp}" ]; then
        if looks_like_clash_yaml "${tmp}"; then
            mv -f "${tmp}" "${out}"
            rm -f "${saved}"
            log_msg "  ✅ 换用 UA「${SUB_UA_FALLBACK}」后拿到 Clash 格式订阅"
            return 0
        fi
    fi
    rm -f "${tmp}"

    # 两次都不是 Clash YAML：先尝试当作 v2ray/v2rayA 类订阅（base64 或明文链接列表）
    # 转换成 Clash 格式 —— 转换成功就正常采用，下游零改动。
    # 找转换脚本：与调用方脚本同目录 → 数据目录（refresh_sub 复制运行的场景）。
    # 注意本文件是被 source 的，$0 是调用者，必须用 BASH_SOURCE 定位自身。
    CONVERT=""
    for _c in "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sub_convert.py" \
              "${TRIM_PKGVAR:-}/cmd/sub_convert.py" \
              "${TRIM_PKGVAR:-}/sub_convert.py"; do
        [ -n "${_c}" ] && [ -f "${_c}" ] && CONVERT="${_c}" && break
    done
    unset _c
    if [ -n "${CONVERT}" ] && command -v python3 >/dev/null 2>&1; then
        if python3 "${CONVERT}" "${saved}" "${out}" 1>&2 2>>"${DEBUG_LOG:-/dev/null}"; then
            rm -f "${saved}"
            log_msg "  ✅ 订阅为 v2ray/链接列表格式，已自动转换为 Clash 格式（$(grep -c '^  - ' "${out}" 2>/dev/null || echo '?') 个节点）"
            return 0
        fi
    fi

    # 转换不可用/失败：绝不能覆盖已有缓存（可能是旧订阅的可用节点），
    # 只把无效内容另存为 .invalid 供诊断 —— 否则「切换订阅失败回滚」后连旧缓存都没了。
    mv -f "${saved}" "${out}.invalid"
    return 2
}
