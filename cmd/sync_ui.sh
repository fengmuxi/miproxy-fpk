#!/bin/bash
#
# MiProxy 面板文件完整性工具（v2.8.11）
#
# 背景：mihomo 的 POST /upgrade/ui（未设 external-ui-url 时）会把内核默认面板
#   MetaCubeXD 解压到 ${ui}/zashboard（external-ui 与 external-ui-name 拼接），
#   旧版升级只「叠加覆盖」，MetaCubeXD 的 sw.js / _nuxt / workbox-* 残留留在
#   数据目录里 → 升级新版 .fpk 后面板照样不恢复（浏览器 Service Worker 还会
#   继续钉住旧文件）。
# 本工具「先删后拷」整体重置数据目录的面板，带完整性守卫与失败回滚：
#   1) 定位源目录（优先与脚本同构建的 ../ui → 安装目录 → 常见路径）
#   2) 源完整性检查（缺关键文件 → 不动目标，exit 2）
#   3) 备份当前 ui → ui.bak-<ts>
#   4) rm -rf 已知子目录/文件 → 从源整体拷入
#   5) 校验目标（失败 → 从备份回滚，exit 3）
#   6) 成功后清理旧备份（只留最近一次）
#
# 用法：
#   bash sync_ui.sh
# 环境变量：
#   TRIM_PKGVAR   数据目录（必须，ui/ 在其中）
#   TRIM_APPDEST  安装目录（候选源）
#   SYNC_UI_SRC   源目录覆盖（测试用）
#
# 已知布局与 install_callback / app/ui 保持一致：
#   子目录 zashboard / metacubexd / admin / images
#   顶层文件 index.html / frame-zashboard.html / frame-metacubexd.html / config

set +e

UI_TARGET="${TRIM_PKGVAR}/ui"
SRC_OVERRIDE="${SYNC_UI_SRC:-}"
LOG_FILE="${TRIM_PKGVAR}/sync_ui.log"

say() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') - $1"
    echo "${line}"
    [ -n "${LOG_FILE}" ] && echo "${line}" >>"${LOG_FILE}" 2>/dev/null
}

UI_SUBS="zashboard metacubexd admin images"
UI_TOP_FILES="index.html frame-zashboard.html frame-metacubexd.html config"

# 目标目录自身必须存在且健康（脚本只重置已知布局，拒绝操作陌生目录）
target_healthy() {
    local t="$1"
    [ -d "${t}" ] || return 1
    [ -f "${t}/index.html" ] || return 1
    [ -f "${t}/zashboard/index.html" ] || [ -f "${t}/metacubexd/index.html" ] || return 0
    return 0
}

# 源目录必须健康：有选择页 + 至少一个面板
src_healthy() {
    local s="$1"
    [ -f "${s}/index.html" ] || return 1
    [ -f "${s}/zashboard/index.html" ] || [ -f "${s}/metacubexd/index.html" ] || return 1
    return 0
}

[ -n "${TRIM_PKGVAR}" ] || { say "错误: 未设置 TRIM_PKGVAR，无法定位数据目录"; exit 5; }

# ---- 1) 定位源目录 -------------------------------------------------------
resolve_src() {
    local c
    if [ -n "${SRC_OVERRIDE}" ]; then
        c="${SRC_OVERRIDE}"
        if [ -d "${c}" ]; then echo "${c}"; return 0; fi
        say "错误: SYNC_UI_SRC 指定的源目录不存在: ${c}"
        return 1
    fi
    for c in "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../ui" \
             "${TRIM_APPDEST}/ui" \
             "/var/apps/miproxy/ui" \
             "/var/packages/miproxy/ui"; do
        [ -n "${c}" ] && [ -d "${c}" ] || continue
        # 源与目标是同一目录时跳过（防止自删自拷）
        local cs ts
        cs="$(cd "${c}" 2>/dev/null && pwd)"
        ts="$(cd "${UI_TARGET}" 2>/dev/null && pwd)"
        [ -n "${cs}" ] && [ "${cs}" = "${ts}" ] && continue
        echo "${cs}"; return 0
    done
    return 1
}

SRC="$(resolve_src)" || exit 2
[ -n "${SRC}" ] || { say "错误: 未找到面板源目录（已尝试: SYNC_UI_SRC、脚本旁 ../ui、TRIM_APPDEST/ui、/var/apps、/var/packages）"; exit 2; }
say "sync_ui: 源目录 ${SRC}"

src_healthy "${SRC}" || { say "错误: 源目录缺少关键文件（index.html 或面板 index.html），已中止"; exit 2; }

mkdir -p "${UI_TARGET}" 2>/dev/null

# ---- 3) 备份 ------------------------------------------------------------
BAK=""
if [ -d "${UI_TARGET}" ]; then
    BAK="${UI_TARGET}.bak-$(date '+%Y%m%d%H%M%S')"
    if cp -a "${UI_TARGET}" "${BAK}" 2>/dev/null; then
        say "sync_ui: 已备份当前面板 → ${BAK}"
    else
        say "错误: 备份失败（磁盘满/权限），为避免数据丢失已中止"
        exit 3
    fi
else
    say "sync_ui: 目标目录不存在（首次部署或被清理），跳过备份直接拷贝"
fi

# ---- 4) 先删后拷 --------------------------------------------------------
reset_failed=0
for _sub in ${UI_SUBS}; do
    rm -rf "${UI_TARGET}/${_sub}" 2>/dev/null
    if [ -d "${SRC}/${_sub}" ]; then
        mkdir -p "${UI_TARGET}/${_sub}" 2>/dev/null
        cp -rf "${SRC}/${_sub}/"* "${UI_TARGET}/${_sub}/" 2>/dev/null || reset_failed=1
    fi
done
for _f in ${UI_TOP_FILES}; do
    [ -f "${SRC}/${_f}" ] || continue
    cp -f "${SRC}/${_f}" "${UI_TARGET}/${_f}" 2>/dev/null || reset_failed=1
done

# ---- 5) 校验目标 --------------------------------------------------------
if [ "${reset_failed}" = "1" ]; then
    say "错误: 重置不完整（拷贝失败），正在从备份回滚"
    rm -rf "${UI_TARGET}" 2>/dev/null
    [ -n "${BAK}" ] && cp -a "${BAK}" "${UI_TARGET}" 2>/dev/null
    say "sync_ui: 已回滚（请检查磁盘空间与权限后重试）"
    exit 3
fi

# ---- 6) 清理旧备份（保留最近一次）----------------------------------------
if [ -n "${BAK}" ]; then
    ls -1dt "${UI_TARGET}".bak-* 2>/dev/null | tail -n +2 | while read -r old; do
        [ "${old}" = "${BAK}" ] && continue
        rm -rf "${old}"
    done
fi

say "sync_ui: 面板文件重置完成（${SRC} → ${UI_TARGET}）"
exit 0
