#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sub_convert.py — 把 v2ray / v2rayA 类订阅（base64 或明文的分享链接列表）
转换成 Clash/mihomo 格式的订阅 YAML。

为什么需要：sub_download.sh 已经能识别「拿回来的不是 Clash YAML」并换 UA 重试，
但很多订阅服务（包括 v2rayA 的 /api/v1/client/subscribe 地址）返回的是
base64 编码的 vmess/vless/ss/trojan(/ssr) 分享链接列表——内核与本项目既有的
extract_yaml_block 流水线都只认 Clash YAML。与其在每个消费端各自实现解析，
不如在「下载入口」统一转换：sub_download.sh 看到非 Clash 内容就调本脚本，
转换成功后下游（install_callback / config_callback / refresh_sub）零改动。

用法：python3 sub_convert.py <输入文件> <输出文件>
退出码：0=转换成功（输出为 Clash/mihomo YAML）；2=无法识别（不是链接列表）
注意：不依赖 PyYAML（fnOS 的 python3 不保证有三方库），YAML 由本脚本手工生成。
"""

import base64
import json
import re
import sys


def _b64_decode(s):
    """容错的 base64 解码：剥空白、补 padding、标准/urlsafe 两种字母表都试。"""
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'[^A-Za-z0-9+/=_-]', '', s)
    s += '=' * (-len(s) % 4)
    last = None
    for fn in (lambda x: base64.b64decode(x, validate=False),
               lambda x: base64.urlsafe_b64decode(x)):
        try:
            data = fn(s)
        except Exception as exc:
            last = exc
            continue
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            continue
    raise ValueError('base64 解码失败: %s' % last)


def _to_int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 链接解析
# 各解析器返回 (proxy_dict, 错误信息)；proxy_dict 里字段顺序即 YAML 输出顺序。

def parse_vmess(link):
    try:
        cfg = json.loads(_b64_decode(link[len('vmess://'):]))
    except Exception as exc:
        return None, 'vmess 链接的 base64/JSON 无法解析（%s）' % exc
    server = str(cfg.get('add') or '').strip()
    port = _to_int(cfg.get('port'))
    uuid = str(cfg.get('id') or '').strip()
    if not server or not port or not uuid:
        return None, 'vmess 节点缺少 add/port/id'
    net = str(cfg.get('net') or 'tcp').strip().lower()
    if net in ('kcp', 'quic'):
        return None, 'vmess 节点使用了 mihomo 不支持的传输层（%s），已跳过' % net
    tls = str(cfg.get('tls') or '').lower() == 'tls'
    sni = str(cfg.get('sni') or cfg.get('host') or '').strip()
    proxy = {
        'name': str(cfg.get('ps') or '').strip(),
        'type': 'vmess',
        'server': server,
        'port': port,
        'uuid': uuid,
        'alterId': _to_int(cfg.get('aid'), 0),
        'cipher': str(cfg.get('scy') or 'auto'),
        'udp': True,
        'network': net,
    }
    if tls:
        proxy['tls'] = True
        if sni:
            proxy['servername'] = sni
    proxy.update(_net_opts(net, str(cfg.get('path') or ''), str(cfg.get('host') or '')))
    return proxy, None


def parse_vless(link):
    import urllib.parse as urlparse
    try:
        u = urlparse.urlsplit(link)
        q = dict(urlparse.parse_qsl(u.query, keep_blank_values=True))
    except Exception as exc:
        return None, 'vless 链接解析失败（%s）' % exc
    try:
        port = u.port
    except ValueError:
        port = 0
    if not u.hostname or not port:
        return None, 'vless 节点缺少 host/port'
    net = (q.get('type') or 'tcp').lower()
    if net in ('kcp', 'quic'):
        return None, 'vless 节点使用了 mihomo 不支持的传输层（%s），已跳过' % net
    proxy = {
        'name': (urlparse.unquote(u.fragment) if u.fragment else '').strip(),
        'type': 'vless',
        'server': u.hostname,
        'port': port,
        'uuid': urlparse.unquote(u.username or ''),
        'udp': True,
        'network': net,
    }
    proxy.update(_net_opts(net, q.get('path', ''), q.get('host', ''),
                           service_name=q.get('serviceName', '')))
    sni = q.get('sni', '')
    sec = (q.get('security') or '').lower()
    if sec in ('tls', 'reality'):
        proxy['tls'] = True
        if sni:
            proxy['servername'] = sni
        if q.get('fp'):
            proxy['client-fingerprint'] = q['fp']
        if sec == 'reality':
            ro = {}
            if q.get('pbk'):
                ro['public-key'] = q['pbk']
            if q.get('sid'):
                ro['short-id'] = q['sid']
            if ro:
                proxy['reality-opts'] = ro
    if q.get('flow'):
        proxy['flow'] = q['flow']
    if str(q.get('allowInsecure', '')).lower() in ('1', 'true'):
        proxy['skip-cert-verify'] = True
    return proxy, None


def parse_ss(link):
    import urllib.parse as urlparse
    body = link[len('ss://'):].split('#', 1)
    name = (urlparse.unquote(body[1]) if len(body) > 1 else '').strip()
    rest = body[0].strip().strip('/')
    try:
        if '@' in rest:
            # SIP002: ss://base64(method:password)@host:port#name
            userinfo, hostport = rest.rsplit('@', 1)
            if ':' not in userinfo:
                userinfo = _b64_decode(userinfo)
            method, _, password = userinfo.partition(':')
        else:
            # 旧格式: ss://base64(method:password@host:port)#name
            decoded = _b64_decode(rest)
            userinfo, _, hostport = decoded.rpartition('@')
            method, _, password = userinfo.partition(':')
    except Exception as exc:
        return None, 'ss 链接解析失败（%s）' % exc
    host, _, port_s = hostport.rpartition(':')
    port = _to_int(port_s)
    if not host or not port:
        return None, 'ss 节点缺少 host/port'
    if host.startswith('['):
        host = host.strip('[]')  # IPv6
    method = method.strip()
    if not method:
        return None, 'ss 节点缺少加密方式'
    return {
        'name': name, 'type': 'ss', 'server': host, 'port': port,
        'cipher': method, 'password': password.strip(), 'udp': True,
    }, None


def parse_trojan(link):
    import urllib.parse as urlparse
    try:
        u = urlparse.urlsplit(link)
        q = dict(urlparse.parse_qsl(u.query, keep_blank_values=True))
    except Exception as exc:
        return None, 'trojan 链接解析失败（%s）' % exc
    try:
        port = u.port
    except ValueError:
        port = 0
    if not u.hostname or not port:
        return None, 'trojan 节点缺少 host/port'
    proxy = {
        'name': (urlparse.unquote(u.fragment) if u.fragment else '').strip(),
        'type': 'trojan',
        'server': u.hostname,
        'port': port,
        'password': urlparse.unquote(u.username or ''),
        'udp': True,
    }
    if q.get('sni'):
        proxy['sni'] = q['sni']
    if q.get('fp'):
        proxy['client-fingerprint'] = q['fp']
    net = (q.get('type') or 'tcp').lower()
    svc = q.get('serviceName') or q.get('path') or ''
    if net == 'grpc' and svc:
        proxy['network'] = 'grpc'
        proxy['grpc-opts'] = {'grpc-service-name': svc}
    if str(q.get('allowInsecure', '')).lower() in ('1', 'true'):
        proxy['skip-cert-verify'] = True
    return proxy, None


def parse_ssr(link):
    import urllib.parse as urlparse
    try:
        main = _b64_decode(link[len('ssr://'):])
    except Exception as exc:
        return None, 'ssr 链接 base64 解析失败（%s）' % exc
    main, _, qs = main.partition('/?')
    parts = main.split(':')
    if len(parts) < 6:
        return None, 'ssr 链接字段不足'
    host = ':'.join(parts[:-5])  # IPv6 的冒号会把 host 拆碎，重组
    port_s, proto, method, obfs, pwd_b64 = parts[-5:]
    try:
        password = _b64_decode(pwd_b64) if pwd_b64 else ''
    except Exception:
        password = pwd_b64
    try:
        q = dict(urlparse.parse_qsl(qs, keep_blank_values=True))
    except Exception:
        q = {}
    port = _to_int(port_s)
    if not host or not port or not method:
        return None, 'ssr 节点缺少 host/port/method'
    proxy = {
        'name': q.get('remarks', ''), 'type': 'ssr', 'server': host,
        'port': port, 'cipher': method, 'password': password,
        'protocol': proto or 'origin', 'obfs': obfs or 'plain', 'udp': True,
    }
    try:
        if q.get('protoparam'):
            proxy['protocol-param'] = _b64_decode(q['protoparam'])
        if q.get('obfsparam'):
            proxy['obfs-param'] = _b64_decode(q['obfsparam'])
    except Exception:
        pass
    # remarks（节点名）同样是 base64：解不开就当明文用（容错旧实现）
    name = q.get('remarks', '')
    if name:
        try:
            name = _b64_decode(name)
        except Exception:
            pass
        proxy['name'] = name
    return proxy, None


PARSERS = {
    'vmess': parse_vmess,
    'vless': parse_vless,
    'ss': parse_ss,
    'trojan': parse_trojan,
    'ssr': parse_ssr,
}


# ---------------------------------------------------------------- YAML 生成

def _ysc(v):
    """标量转 YAML 文本：字符串用 json.dumps（JSON 字符串是合法的 YAML 双引号标量）。"""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def _emit_mapping(d, indent, out):
    pad = ' ' * indent
    for k, v in d.items():
        if isinstance(v, dict):
            out.append('%s%s:' % (pad, k))
            _emit_mapping(v, indent + 2, out)
        else:
            out.append('%s%s: %s' % (pad, k, _ysc(v)))


def _net_opts(net, path, host, service_name=''):
    """传输层参数 → mihomo 的 *-opts 字段（没有则返回空 dict）。"""
    opts = {}
    if net == 'ws':
        w = {'path': path or '/'}
        if host:
            w['headers'] = {'Host': host}
        opts['ws-opts'] = w
    elif net == 'grpc':
        svc = service_name or path
        if svc:
            opts['grpc-opts'] = {'grpc-service-name': svc.split('/')[0]}
    elif net == 'h2':
        h = {'path': path or '/'}
        if host:
            h['host'] = [host]
        opts['h2-opts'] = h
    elif net == 'http':
        opts['http-opts'] = {'path': [path or '/']}
    return opts


def default_name(proxy):
    return '%s-%s:%s' % (proxy['type'], proxy['server'], proxy['port'])


def main():
    if len(sys.argv) != 3:
        print('用法: sub_convert.py <输入文件> <输出文件>', file=sys.stderr)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    try:
        raw = open(src, 'rb').read()
    except OSError as exc:
        print('读取输入失败（%s）' % exc, file=sys.stderr)
        return 2
    text = raw.decode('utf-8', 'replace').lstrip('\ufeff').strip()
    if not text:
        print('输入内容为空', file=sys.stderr)
        return 2

    # 本来就是 Clash YAML（grep 漏判多因 BOM / 顶格 formatting）：去 BOM 原样输出
    if re.search(r'^\s*(proxies|proxy-providers)\s*:', text, re.M):
        with open(dst, 'w', encoding='utf-8') as fh:
            fh.write(text)
        print('输入本来就是 Clash YAML，已原样采用（去 BOM）', file=sys.stderr)
        return 0

    # base64 整体解码；已经是明文链接列表则直接用
    content = text
    if not re.search(r'^[a-z][a-z0-9+.-]*://', text, re.I):
        try:
            content = _b64_decode(text)
        except Exception:
            print('内容既不是 Clash YAML，也不是 base64/链接列表', file=sys.stderr)
            return 2

    warnings = []
    proxies = []
    seen = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or '://' not in line:
            continue
        scheme = line.split('://', 1)[0].lower()
        parser = PARSERS.get(scheme)
        if parser is None:
            warnings.append('暂不支持 %s:// 链接，已跳过' % scheme)
            continue
        proxy, err = parser(line)
        if err or not proxy:
            warnings.append(err or '节点解析失败')
            continue
        base = proxy['name'] or default_name(proxy)
        name, i = base, 2
        while name in seen:
            name = '%s-%d' % (base, i)
            i += 1
        seen.add(name)
        proxy['name'] = name
        proxies.append(proxy)

    if not proxies:
        print('没有可用的节点链接（%s）' % ('；'.join(warnings) if warnings else '空列表'),
              file=sys.stderr)
        return 2

    node_names = [p['name'] for p in proxies]
    out = [
        '# 该订阅由链接列表自动转换生成（cmd/sub_convert.py，勿手改）',
        'proxies:',
    ]
    for p in proxies:
        items = list(p.items())
        for idx, (k, v) in enumerate(items):
            prefix = '  - ' if idx == 0 else '    '
            if isinstance(v, dict):
                out.append('%s%s:' % (prefix, k))
                _emit_mapping(v, 6, out)
            else:
                out.append('%s%s: %s' % (prefix, k, _ysc(v)))
    auto_name = '自动选择'
    out.append('proxy-groups:')
    out.append('  - name: PROXY')
    out.append('    type: select')
    out.append('    proxies:')
    out.append('      - ' + _ysc(auto_name))
    for n in node_names:
        out.append('      - ' + _ysc(n))
    out.append('      - DIRECT')
    out.append('  - name: ' + _ysc(auto_name))
    out.append('    type: url-test')
    out.append('    url: "http://www.gstatic.com/generate_204"')
    out.append('    interval: 300')
    out.append('    tolerance: 50')
    out.append('    proxies:')
    for n in node_names:
        out.append('      - ' + _ysc(n))
    out.append('rules:')
    out.append('  - "GEOIP,CN,DIRECT"')
    out.append('  - "MATCH,PROXY"')

    with open(dst, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(out) + '\n')

    print('成功转换 %d 个节点（%s）' % (len(proxies), '；'.join(warnings) if warnings else '无跳过'),
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
