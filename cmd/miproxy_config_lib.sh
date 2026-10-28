#!/bin/bash
# ============================================================================
# miproxy_config_lib.sh — install_callback 与 config_callback 共用的配置生成库
# ----------------------------------------------------------------------------
# 为什么存在：
#   config.yaml 完全由脚本用 echo 拼接生成，过去在 install_callback 与
#   config_callback 里各有一份「字段抽取 + config.yaml 生成」副本（尚未抽公共库）。
#   改一处必须同步另一处，否则会出现「安装出来的配置对、改配置后不对」（或反之）的
#   诡异现象（历史 bug 多源于「只修了一份」）。现在所有与「订阅解析 / config.yaml
#   生成」相关的逻辑都收拢到这里，两个 callback 只负责「下载订阅 / 写 settings.conf /
#   重启内核 / 进度上报」等各自差异化的流程，生成核心统一调用本库。
#
# 使用方式：在两个 callback 里 `source "${SCRIPT_DIR}/miproxy_config_lib.sh"`
#   （SCRIPT_DIR 指脚本自身所在目录，本库与它同目录，会被一起打包/同步）。
#
# 约定：
#   - 本库不执行任何「副作用」（不写文件、不起进程、不改配置），只提供纯函数。
#   - 调用方负责设置必要的输入变量（见各函数注释），并在调用后做自己的落盘 / 校验。
#   - python3 heredoc 统一用引号定界符，避免变量在 shell 层被展开。
# ============================================================================

# ----------------------------------------------------------------------------
# extract_yaml_block <顶层键> <yaml文件>
# 按「顶层键」从订阅源 YAML 抽取整块（proxies / proxy-groups / rules / dns /
# proxy-providers 等十几个字段）。抽取后必须过一遍 normalize() 统一缩进：
# 订阅源常见的「顶格 - 」写法要缩进到父键下面，输出的 config.yaml 才是规范 YAML。
# 解析失败不再静默丢弃：stderr 统一写入 info.log（python3 缺失/文件不可读/语法
# 错误均可追溯）。返回抽取到的文本块（stdout）。
# ----------------------------------------------------------------------------
extract_yaml_block() {
    python3 - "$1" "$2" 2>>"${LOG_FILE:-/dev/null}" <<'PYEOF'
import sys
key = sys.argv[1] + ":"
try:
    with open(sys.argv[2], 'r', encoding='utf-8') as f:
        lines = f.readlines()
except Exception as e:
    sys.stderr.write("[extract_yaml_block] 读取订阅缓存失败: %s (%s: %s)\n" % (sys.argv[2], type(e).__name__, e))
    sys.exit(3)
result = []
found = False
for line in lines:
    stripped = line.rstrip('\n')
    if not stripped.strip():
        if found: result.append(line)
        continue
    is_top = not stripped[0].isspace()
    if is_top:
        if stripped.startswith(key):
            found = True
            result.append(line)
        elif found:
            if stripped.startswith('-'):
                result.append(line)
            else:
                break
    elif found:
        result.append(line)


def normalize(block):
    """把「顶格 - 」的列表项缩进到父键下面，输出规范的 YAML 块。

    为什么必须做：YAML 允许序列与父键同缩进，所以
        proxies:
        - name: A
    对内核是合法的（节点能正常加载），但一堆简易解析器
    （包括本项目早期版本的管理服务）是按「块内必须缩进」来切段的，
    遇到顶格就认为 proxies 段结束 —— 于是订阅明明有 80 多个节点，
    管理界面却显示 0 个，用户完全看不出问题在哪。
    统一缩进成规范写法，两边就都对了。
    """
    if len(block) <= 1:
        return block
    body = block[1:]
    min_indent = None
    for l in body:
        if not l.strip():
            continue
        indent = len(l) - len(l.lstrip(' '))
        if min_indent is None or indent < min_indent:
            min_indent = indent
    if min_indent is None or min_indent > 0:
        return block          # 本来就是缩进好的，不用动
    out = [block[0]]
    for l in body:
        out.append(('  ' + l) if l.strip() else l.rstrip('\n'))
    return out


sys.stdout.write(''.join(normalize(result)))
PYEOF
}

# ----------------------------------------------------------------------------
# count_yaml_nodes <yaml文件>
# 统计一个 YAML 文件里 proxies 段的条目数（顶格/缩进两种写法都认）。
# 解析失败返回 0（不抛错，调用方据此决定保护策略）。
# ----------------------------------------------------------------------------
count_yaml_nodes() {
    python3 - "$1" 2>>"${LOG_FILE:-/dev/null}" <<'PYEOF'
import sys
path = sys.argv[1]
try:
    lines = open(path, encoding='utf-8', errors='replace').read().splitlines()
except Exception:
    print("0")
    sys.exit(0)
inblk = False
count = 0
for raw in lines:
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
print(count)
PYEOF
}

# ----------------------------------------------------------------------------
# diagnose_subscription <缓存文件>
# 订阅启用了却一个节点都没解析出来时，说清楚到底是哪种格式问题
# （HTML / JSON / base64 / 缺段），便于在日志/接口里给出可读的诊断。
# 输出诊断文本到 stdout。
# ----------------------------------------------------------------------------
diagnose_subscription() {
    python3 - "${1}" 2>>"${LOG_FILE:-/dev/null}" <<'PYEOF'
import sys, re
p = sys.argv[1]
try:
    data = open(p, 'rb').read()
except Exception as e:
    print("订阅缓存读取失败（%s）" % e)
    sys.exit(0)
if not data:
    print("订阅缓存是空文件")
    sys.exit(0)
text = data.decode('utf-8', 'replace')
head = ' '.join(text[:120].split())
size = len(data)
stripped = text.strip()
if re.match(r'^\s*(<\?xml|<!DOCTYPE|<html)', text, re.I):
    print("订阅返回的是 HTML 网页而不是配置（链接可能需要 User-Agent/令牌，或被中间层劫持）；开头：%s" % head)
elif stripped.startswith('{') or stripped.startswith('['):
    print("订阅返回的是 JSON 而不是 Clash YAML；开头：%s" % head)
elif ':' not in stripped and re.match(r'^[A-Za-z0-9+/=\s]{40,}$', stripped):
    print("订阅返回的是 base64 编码内容（多半是 v2ray/ss 链接），需先转换成 Clash/mihomo 格式；开头：%s" % head[:60])
elif 'proxies:' not in text and 'proxy-providers:' not in text:
    print("订阅里没有 proxies: / proxy-providers: 段（共 %d 字节，开头：%s）" % (size, head))
else:
    print("订阅格式可疑（共 %d 字节，开头：%s）" % (size, head))
PYEOF
}

# ----------------------------------------------------------------------------
# config_has_nodes
# 当前 config.yaml 里还有没有节点（用于决定「旧配置能否被覆盖」）。
# 复用 count_yaml_nodes：它认得「顶格 - 」和「缩进 - 」两种写法，
# 早前这里只认缩进，顶格写法会被误判成「没有节点」，保护逻辑形同虚设。
# 输出 yes / no。
# ----------------------------------------------------------------------------
config_has_nodes() {
    local n
    n=$(count_yaml_nodes "${CONFIG_FILE}")
    if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
        echo "yes"
    else
        echo "no"
    fi
}

# ----------------------------------------------------------------------------
# compute_domain_filter
# 输入（调用前须设置）：SUB_TYPE、SUB_URL（远程订阅地址）。
# 输出（导出）：SUB_DOMAIN_FILTER（fake-ip 白名单 YAML 片段）+ domain（纯域名）。
# 安全：先 sed 去掉协议/路径/端口，再用 grep -oE 严格校验是合法域名，
#       杜绝把任意字符串拼进 YAML（命令注入防护）。
# ----------------------------------------------------------------------------
compute_domain_filter() {
    SUB_DOMAIN_FILTER=""
    domain=""
    if [ "${SUB_TYPE}" = "remote" ] && [ -n "${SUB_URL}" ]; then
        domain=$(echo "${SUB_URL}" | sed -E 's|^https?://||; s|/.*$||; s|:.*$||' | grep -oE '^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$' | head -1)
        if [ -n "${domain}" ]; then
            SUB_DOMAIN_FILTER="    - \"${domain}\"\n    - \"*.${domain}\""
        fi
    fi
    export SUB_DOMAIN_FILTER domain
}

# ----------------------------------------------------------------------------
# extract_subscription_fields
# 前置（调用前须设置）：SUB_CACHE_PATH、HAVE_PY3、SUB_TYPE。
# 输出（导出）：SUB_PROXIES/SUB_HOSTS/SUB_PG/SUB_RULES/SUB_DNS 及扩展字段
#   （SUB_BYPASS/SUB_PROXY_PROVIDERS/SUB_RULE_PROVIDERS/SUB_SUB_RULE/
#    SUB_TUNNELS/SUB_LISTENERS/SUB_SNIFF/SUB_NTP/SUB_SCRIPT）。
# 只有启用订阅（remote/local）且 python3 可用且缓存存在时才抽取；
# 否则所有字段保持空。
# ----------------------------------------------------------------------------
extract_subscription_fields() {
    SUB_PROXIES=""; SUB_HOSTS=""; SUB_PG=""; SUB_RULES=""; SUB_DNS=""
    SUB_BYPASS=""; SUB_PROXY_PROVIDERS=""; SUB_RULE_PROVIDERS=""; SUB_SUB_RULE=""
    SUB_TUNNELS=""; SUB_LISTENERS=""; SUB_SNIFF=""; SUB_NTP=""; SUB_SCRIPT=""
    # HAVE_PY3 未显式设置时自动探测（callback 已自行探测并写日志，不受影响）
    if [ -z "${HAVE_PY3+X}" ]; then
        if command -v python3 >/dev/null 2>&1; then
            HAVE_PY3=1
        else
            HAVE_PY3=0
        fi
    fi
    if [ -f "${SUB_CACHE_PATH}" ] && [ "${HAVE_PY3:-0}" = "1" ] && { [ "${SUB_TYPE}" = "remote" ] || [ "${SUB_TYPE}" = "local" ]; }; then
        SUB_PROXIES=$(extract_yaml_block "proxies" "${SUB_CACHE_PATH}")
        SUB_HOSTS=$(extract_yaml_block "hosts" "${SUB_CACHE_PATH}")
        SUB_PG=$(extract_yaml_block "proxy-groups" "${SUB_CACHE_PATH}")
        SUB_RULES=$(extract_yaml_block "rules" "${SUB_CACHE_PATH}")
        SUB_DNS=$(extract_yaml_block "dns" "${SUB_CACHE_PATH}")
        SUB_BYPASS=$(extract_yaml_block "bypass" "${SUB_CACHE_PATH}")
        SUB_PROXY_PROVIDERS=$(extract_yaml_block "proxy-providers" "${SUB_CACHE_PATH}")
        SUB_RULE_PROVIDERS=$(extract_yaml_block "rule-providers" "${SUB_CACHE_PATH}")
        SUB_SUB_RULE=$(extract_yaml_block "sub-rule" "${SUB_CACHE_PATH}")
        SUB_TUNNELS=$(extract_yaml_block "tunnels" "${SUB_CACHE_PATH}")
        SUB_LISTENERS=$(extract_yaml_block "listeners" "${SUB_CACHE_PATH}")
        SUB_SNIFF=$(extract_yaml_block "sniff" "${SUB_CACHE_PATH}")
        SUB_NTP=$(extract_yaml_block "ntp" "${SUB_CACHE_PATH}")
        SUB_SCRIPT=$(extract_yaml_block "script" "${SUB_CACHE_PATH}")
        # dns 缺 nameserver 时丢弃，回退本地默认
        [ -n "${SUB_DNS}" ] && ! printf '%s' "${SUB_DNS}" | grep -q "nameserver" && SUB_DNS=""
    fi
    export SUB_PROXIES SUB_HOSTS SUB_PG SUB_RULES SUB_DNS \
           SUB_BYPASS SUB_PROXY_PROVIDERS SUB_RULE_PROVIDERS SUB_SUB_RULE \
           SUB_TUNNELS SUB_LISTENERS SUB_SNIFF SUB_NTP SUB_SCRIPT
}

# ----------------------------------------------------------------------------
# emit_config_yaml
# 依赖（调用前须已设置）：HTTP_PORT API_PORT SECRET PROXY_USER PROXY_PASS
#   TUN_ENABLE UI_TARGET；以及 SUB_DOMAIN_FILTER/domain 与 SUB_PROXIES 等抽取字段。
# 输出：把完整 config.yaml 写入 ${CONFIG_TMP}（调用方负责校验节点数后落盘）。
# 这是 install_callback 与 config_callback 唯一的 config.yaml 生成入口，
# 两者因此必然一致（功能等价、flow/block 风格也统一为 inline 数组写法）。
# ----------------------------------------------------------------------------
emit_config_yaml() {
    {
        echo "# MiProxy 主配置（由 cmd/miproxy_config_lib.sh 统一生成）"
        echo ""
        echo "mixed-port: ${HTTP_PORT}"
        echo "allow-lan: true"
        echo "mode: rule"
        echo "log-level: info"
        echo "ipv6: false"
        echo "external-controller: 0.0.0.0:${API_PORT}"
        echo "external-ui: ${UI_TARGET}"
        echo "external-ui-name: zashboard"
        [ -n "${SECRET}" ] && echo "secret: \"${SECRET}\""
        [ -n "${PROXY_USER}" ] && [ -n "${PROXY_PASS}" ] && echo "authentication:" && echo "  - \"${PROXY_USER}:${PROXY_PASS}\""
        echo "external-controller-cors:"
        echo "  allow-private-network: true"
        echo "  allow-origins: [\"*\"]"
        echo "geodata-mode: true"
        echo "geodata-loader: memconservative"
        echo "tun:"
        echo "  enable: ${TUN_ENABLE}"
        echo "  stack: mixed"
        echo "  auto-route: true"
        echo "  auto-redirect: true"
        echo "  auto-detect-interface: true"
        echo "  device: miproxy"
        echo "  mtu: 1500"
        echo "  dns-hijack: [\"any:53\", \"tcp://any:53\"]"
        echo "  strict-route: true"
        echo "  gso: true"
        echo "  gso-max-size: 65536"
        # ============ 来自订阅源的字段 ============
        [ -n "${SUB_PROXIES}" ] && echo "# 节点列表（来自订阅源）" && echo "${SUB_PROXIES}"
        [ -n "${SUB_HOSTS}" ] && echo "# hosts（来自订阅源）" && echo "${SUB_HOSTS}"
        if [ -n "${SUB_PG}" ]; then
            echo "# 代理组（来自订阅源）"
            echo "${SUB_PG}"
        else
            echo "proxy-groups:"
            echo "  - name: PROXY"
            echo "    type: select"
            echo "    proxies: [DIRECT, REJECT]"
        fi
        if [ -n "${SUB_RULES}" ]; then
            echo "# 路由规则（来自订阅源）"
            echo "${SUB_RULES}"
        else
            echo "rules: [\"GEOIP,CN,DIRECT\", \"MATCH,PROXY\"]"
        fi
        if [ -n "${SUB_DNS}" ]; then
            echo "# DNS（来自订阅源）"
            echo "${SUB_DNS}"
        else
            echo "dns:"
            echo "  enable: true"
            echo "  listen: 0.0.0.0:53"
            echo "  enhanced-mode: fake-ip"
            echo "  fake-ip-range: 198.18.0.1/16"
            echo "  fake-ip-filter: [\"+.lan\", \"+.local\"${SUB_DOMAIN_FILTER:+, \"${domain}\", \"*.${domain}\"}]"
            echo "  nameserver: [223.5.5.5, 119.29.29.29, 8.8.8.8]"
        fi
        [ -n "${SUB_BYPASS}" ] && echo "# bypass 私有绕过列表（来自订阅源）" && echo "${SUB_BYPASS}"
        [ -n "${SUB_PROXY_PROVIDERS}" ] && echo "# proxy-providers 代理集合（来自订阅源）" && echo "${SUB_PROXY_PROVIDERS}"
        [ -n "${SUB_RULE_PROVIDERS}" ] && echo "# rule-providers 规则集合（来自订阅源）" && echo "${SUB_RULE_PROVIDERS}"
        [ -n "${SUB_SUB_RULE}" ] && echo "# sub-rule 子规则（来自订阅源）" && echo "${SUB_SUB_RULE}"
        [ -n "${SUB_TUNNELS}" ] && echo "# tunnels 流量隧道（来自订阅源）" && echo "${SUB_TUNNELS}"
        [ -n "${SUB_LISTENERS}" ] && echo "# listeners 监听器（来自订阅源）" && echo "${SUB_LISTENERS}"
        [ -n "${SUB_SNIFF}" ] && echo "# sniff 嗅探（来自订阅源）" && echo "${SUB_SNIFF}"
        [ -n "${SUB_NTP}" ] && echo "# ntp 时间同步（来自订阅源）" && echo "${SUB_NTP}"
        [ -n "${SUB_SCRIPT}" ] && echo "# script 脚本（来自订阅源）" && echo "${SUB_SCRIPT}"
    } > "${CONFIG_TMP}"
}
