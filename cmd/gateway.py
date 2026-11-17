#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gateway.py — MiProxy 的 9090 统一入口网关（TCP 层按路径分发）。

为什么需要它：
  fn 链接（飞牛远程访问）只把 manifest 声明的 service_port（9090）映射出去。
  过去 9090 上只有 mihomo（external-controller + external-ui），管理界面在 9091，
  而面板选择页里「MiProxy 管理」卡片硬编码跳 hostname:9091 —— 远程访问时
  9091 不在映射范围内，直接 ERR_CONNECTION_REFUSED。
  现在由本网关占用 9090：按每个连接的首个 HTTP 请求路径分发——
    /api/*、/admin*  → 127.0.0.1:9091（admin_server 管理服务）
    其余（/ui/*、/configs、/proxies、/traffic ...）→ 127.0.0.1:9092（mihomo 内部控制器）

实现要点（为什么不做成 HTTP 反代而做成 TCP 透传）：
  mihomo 的 /traffic、/connections 是 WebSocket，/logs 是流式响应；
  Python 标准库 http.server 做 WebSocket 反代要手动处理协议升级，极易出错。
  TCP 层只解析「每个连接的第一行请求」来选后端，之后字节流双向透传，
  WebSocket / SSE / 长连接天然可用。

keep-alive 陷阱与对策：
  浏览器会复用同一条 TCP 连接先后请求不同路径（先 /configs 再 /api/status），
  而本网关按「连接的第一个请求」定死后端 —— 复用连接就会错路由。
  对策：对非 WebSocket 请求，把请求头里的 Connection 改写成 close，
  后端会随之回 Connection: close，浏览器用完即断，下个请求重新选路。
  WebSocket 请求（带 Upgrade 头）保持原样，连接专用、不受影响。
"""

import os
import select
import socket
import sys
import threading

LISTEN_HOST = os.environ.get("GATEWAY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("GATEWAY_PORT", "9090"))

ADMIN_HOST = os.environ.get("ADMIN_HOST", "127.0.0.1")
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "9091"))

MIHOMO_HOST = os.environ.get("MIHOMO_CTRL_HOST", "127.0.0.1")
MIHOMO_PORT = int(os.environ.get("MIHOMO_CTRL_PORT", "9092"))

LOG_PATH = os.environ.get("GATEWAY_LOG", "")
BUF_SIZE = 65536
HEAD_MAX = 65536
IDLE_TIMEOUT = 600  # 空闲连接保底超时（秒）：长连接泄漏时兜底回收


def log(msg):
    line = msg if msg.endswith("\n") else msg + "\n"
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass
    else:
        sys.stderr.write(line)


def pick_backend(head_bytes):
    """按首个 HTTP 请求行选后端，返回 (host, port)。解析失败视为 mihomo。"""
    try:
        first = head_bytes.split(b"\r\n", 1)[0].decode("latin-1")
        path = first.split(" ")[1]
    except Exception:
        return MIHOMO_HOST, MIHOMO_PORT
    # /api/* 与 /admin*（含 /admin、/admin/、/admin/index.html）→ 管理服务
    if path.startswith("/api/") or path == "/api" or path.startswith("/admin"):
        return ADMIN_HOST, ADMIN_PORT
    return MIHOMO_HOST, MIHOMO_PORT


def force_close(head_bytes):
    """非 WebSocket 请求：剥掉原 Connection 头并注入 Connection: close。

    WebSocket 升级请求（含 Upgrade 头）绝不能动 —— 协议依赖 Connection: Upgrade。
    """
    sep = b"\r\n\r\n"
    if sep not in head_bytes:
        return head_bytes
    head, rest = head_bytes.split(sep, 1)
    lines = head.split(b"\r\n")
    if any(l.lower().startswith(b"upgrade:") for l in lines):
        return head_bytes
    kept = [l for l in lines if not l.lower().startswith(b"connection:")]
    kept.append(b"Connection: close")
    return b"\r\n".join(kept) + sep + rest


def pump(sock_a, sock_b):
    """双向字节透传，任一端断开/出错即结束（两端的 socket 由调用方关闭）。"""
    pair = (sock_a, sock_b)
    while True:
        try:
            r, _, _ = select.select(pair, [], [], IDLE_TIMEOUT)
        except (OSError, ValueError):
            return
        if not r:
            return  # 空闲超时
        for s in r:
            try:
                data = s.recv(BUF_SIZE)
            except (ConnectionError, OSError):
                return
            if not data:
                return
            dst = sock_b if s is sock_a else sock_a
            try:
                dst.sendall(data)
            except (ConnectionError, OSError):
                return


def read_request_head(client):
    """读满请求头（到 \\r\\n\\r\\n 为止）。失败返回 None。"""
    client.settimeout(15)
    buf = b""
    while len(buf) < HEAD_MAX:
        try:
            chunk = client.recv(BUF_SIZE)
        except (ConnectionError, socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
        if b"\r\n\r\n" in buf:
            return buf
    return None  # 超长异常请求，丢弃


def handle(client):
    try:
        head = read_request_head(client)
        if not head:
            client.close()
            return
        host, port = pick_backend(head)
        payload = force_close(head)
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as exc:
            log("upstream %s:%d 不可达: %s" % (host, port, exc))
            try:
                body = ("后端服务不可达（%s:%d），请检查服务是否已启动。" % (host, port)).encode("utf-8")
                client.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Connection: close\r\n"
                    b"Content-Type: text/plain; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            except OSError:
                pass
            client.close()
            return
        upstream.settimeout(None)
        client.settimeout(None)
        try:
            upstream.sendall(payload)
        except OSError:
            client.close()
            upstream.close()
            return
        pump(client, upstream)
    except Exception as exc:  # 兜底：任何异常都不许杀死网关线程
        log("handle 异常: %r" % exc)
    finally:
        try:
            client.close()
        except OSError:
            pass
        try:
            upstream.close()
        except (UnboundLocalError, OSError):
            pass


def serve():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(128)
    log("gateway 已启动: %s:%d  →  admin %s:%d | mihomo %s:%d"
        % (LISTEN_HOST, LISTEN_PORT, ADMIN_HOST, ADMIN_PORT, MIHOMO_HOST, MIHOMO_PORT))
    while True:
        try:
            client, addr = srv.accept()
        except OSError as exc:
            log("accept 异常: %r" % exc)
            continue
        t = threading.Thread(target=handle, args=(client,), daemon=True)
        t.start()


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log("gateway 退出: %r" % exc)
        sys.exit(1)
