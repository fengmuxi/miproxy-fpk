# agent.md — MiProxy 开发指南（给 AI 编码助手）

本文件用于让 AI 编程助手快速、正确地理解并修改 **MiProxy** 仓库。请先读完再动手改代码。

---

## 1. 这是什么项目

**MiProxy** 是一个 [飞牛 fnOS](https://www.fnnas.com/) 的第三方应用包（`.fpk`），把 NAS 变成私有代理网关。

- 内核：[mihomo](https://github.com/MetaCubeX/mihomo)（Clash Meta），以**预编译二进制**形式随包发布。
- 能力：TUN 全局代理、订阅自动刷新、双控制面板（Zashboard / MetaCubeXD）、GeoIP/GeoSite 分流、DNS 防泄露。
- **不是**编译型项目：仓库里没有 `go.mod` / `package.json` 等构建系统。代码主体是 **bash 脚本** + 二进制 + 前端静态资源。改动后“构建”= 重新打包 `.fpk`（见 §7）。

> ⚠️ 本仓库的提交历史里大量出现「🔄 自动同步二进制文件」，这是上游把新版 mihomo / 前端二进制直接提交进来的痕迹，不是常规源码提交，无需在意。

---

## 2. 顶层结构

```
miproxy-fpk/
├── manifest                 # 包元数据（appname/version/端口/权限等），纯 key=value
├── ICON.PNG / ICON_256.PNG  # 应用图标
├── README.md                # 面向用户的产品说明（非技术文档）
├── app/
│   ├── server/<arch>/mihomo # 内核二进制：arm64/ 与 x86_64/ 两个目录
│   ├── data/                # GeoIP.dat / GeoSite.dat / Country.mmdb（分流数据库）
│   └── ui/                  # 前端：zashboard/、metacubexd/、admin/（自带管理页）、index.html（选择页）、config、images/
├── cmd/                     # ★ 主要“源码”：fnOS 生命周期 bash 脚本
│   ├── main                 # 启停管理：start|stop|restart|status（含刷新守护进程 + 管理服务）
│   ├── refresh_sub          # 订阅刷新守护进程（循环下载订阅 + 重启 mihomo）；`once` 为单次“刷新并应用”
│   ├── admin_server.py      # 管理界面后端（Python3 标准库，端口 9091，见 §7）
│   ├── install_callback     # 安装后回调：拷二进制/前端/数据、下订阅、生成 config.yaml、写 settings.conf、启动
│   ├── install_init         # 安装前回调（当前仅 exit 0）
│   ├── config_init          # 配置变更前回调（当前仅 exit 0）
│   ├── config_callback      # 配置变更后回调（重新生成配置并重启）
│   ├── upgrade_init / upgrade_callback  # 升级生命周期
│   └── uninstall_init / uninstall_callback  # 卸载生命周期
├── config/
│   ├── privilege            # JSON：声明以 root 运行（TUN 必需）
│   └── resource             # JSON：数据共享声明
└── wizard/
    ├── install              # 安装向导 UI（JSON 数组，定义表单字段）
    ├── config               # 配置向导 UI（JSON 数组，定义表单字段）
    └── uninstall            # 卸载向导 UI
```

---

## 3. fnOS 环境变量（脚本里到处都用，务必熟悉）

| 变量 | 含义 | 典型值 |
|------|------|--------|
| `TRIM_PKGVAR` | **数据/运行时目录**（config.yaml、日志、mihomo 运行目录） | `/vol1/@appdata/miproxy` |
| `TRIM_PKGETC`  | **配置目录**（settings.conf 所在地） | `/vol1/@appconfig/miproxy` |
| `TRIM_APPDEST` | **应用安装目录**（原始 `app/`、`cmd/` 所在） | `/var/apps/miproxy` |
| `TRIM_TEMP_LOGFILE` | fnOS 识别的错误日志路径（启动失败时报错用） | 由系统注入 |

> 脚本通过 `TRIM_APPDEST` 读取原始资源，把产物写到 `TRIM_PKGVAR`，配置写到 `TRIM_PKGETC`。**不要把路径写死**，要沿用这些变量。

---

## 4. 向导字段 ↔ settings.conf ↔ 脚本 的映射

向导（JSON）里的每个 `field` 在回调脚本中以同名环境变量 **`wizard_*` 传入**。

| wizard 字段 | 说明 | 默认值 | 在 settings.conf 落盘键 | 被谁消费 |
|-------------|------|--------|------------------------|----------|
| `wizard_http_port` | mixed-port 代理端口 | `7890` | `wizard_http_port` | install_callback → config.yaml |
| `wizard_api_port`  | external-controller 面板端口 | `9090` | `wizard_api_port` | install_callback → config.yaml |
| `wizard_proxy_user` / `wizard_proxy_pass` | 代理认证 | 空 | 同键 | install_callback → config.yaml `authentication` |
| `wizard_secret` | 面板 API 密钥 | 空 | `wizard_secret` | install_callback → config.yaml `secret` |
| `wizard_tun_enable` | 是否启用 TUN | `false` | `wizard_tun_enable` | config.yaml `tun.enable` |
| `wizard_sub_type` | 订阅类型：`none`/`remote`/`local` | `none` | `wizard_sub_type` | install_callback / main / refresh_sub |
| `wizard_sub_url` | 远程订阅 URL | 空 | `wizard_sub_url` | 下载订阅 + 刷新守护进程 |
| `wizard_sub_path` | 本地订阅文件路径 | `/vol1/@appshare/miproxy/sub.yaml` | `wizard_sub_path` | 安装时拷贝 |
| `wizard_refresh_interval` | 自动刷新间隔（小时）：`0`/`1`/`6`/`12`/`24` | `6` | `wizard_refresh_interval` | main / refresh_sub（0=禁用） |

- `settings.conf` 是 **key=value** 文本文件，由 `install_callback` 写出，后续 `main` / `refresh_sub` 用 `grep '^key=' | cut -d= -f2-` 读取。
- 另有**非向导键**（手工维护，不要在重写时丢掉）：`wizard_admin_port`（管理服务端口，默认 9091）、`wizard_admin_token`（管理接口备用令牌）。
- **多订阅模型**：`subscriptions.json`（同目录）存订阅清单，`active_id` 指向启用项；但**启用项仍然落盘为 `wizard_sub_type` / `wizard_sub_url` / `wizard_sub_path`**。即：清单是「备选集合」，settings.conf 是「唯一真相源」，所以 `main` / `refresh_sub` / 两个 callback 都不需要认识清单，天然保证「同一时间只有一个订阅生效」。改多订阅逻辑时不要破坏这个分层。
- 新增向导字段的标准流程：①在 `wizard/config`（或 `wizard/install`）加 JSON 项 → ②在 `install_callback` 顶部加 `XXX="${wizard_xxx:-默认}"` 读取 → ③写入 `settings.conf` → ④在 `config.yaml` 生成块里使用。若影响运行逻辑，还要同步 `config_callback`。

---

## 5. 配置生成逻辑（最容易改错的地方）

`config.yaml` **完全由脚本用 `echo` 拼接生成**，不是手写文件。⚠️ 这段生成逻辑在 `install_callback` 与 `config_callback` 里**各有一份副本（尚未抽公共库）**，改一处必须同步另一处，否则会出现「安装出来的配置对、改配置后不对」（或反之）的诡异现象。关键点：

1. **两份生成逻辑必须等价**：字段列表、TUN 参数、settings.conf 键、订阅解析 heredoc 三处都要对齐。改完用「同一份订阅分别跑两个脚本，再 diff 生成的 config.yaml」验证（注释行与 flow/block 风格差异可忽略，其余应一致）。
2. **订阅解析**用一段内嵌的 `python3` heredoc `extract_yaml_block(key, file)`，按“顶层键”从订阅源 YAML 抽取整块（proxies / proxy-groups / rules / dns / proxy-providers 等十几个字段）。改动解析逻辑务必保持“保留订阅源原始格式”的约定（历史 bug 多源于此）。
3. **DNS 兜底**：若订阅自带 `dns:` 块但缺 `nameserver`，则丢弃改用本地默认 DNS（见 install_callback 末尾对 `SUB_DNS` 的处理）。
4. **fake-ip-filter**：远程订阅时会用 `sed`+`grep -oE` 从 URL 提取合法域名做白名单（`SUB_DOMAIN_FILTER`），这是**防命令注入**的安全处理，不要改成直接拼接 URL。
5. TUN 网卡名固定为 `miproxy`，`strict-route: true`、`gso: true` 是 v2.7 的防 DNS 泄露 / 性能优化项，改动需谨慎。
6. mihomo 启动命令：`${MIHOMO_BIN} -d ${MIHOMO_DIR} -f ${MIHOMO_CONFIG}`（`-d` 指定运行目录，含数据库与 proxy_providers）。
7. **节点是内嵌进 `config.yaml` 的，不是 `proxy-provider` 引用**。因此“刷新订阅”必须走三步：下载缓存 → 重新生成 `config.yaml` → 重启内核。少任一步都等于没刷新（历史 bug 就出在这里）。
8. **`MIPROXY_NO_RESTART=1`**：`config_callback` 末尾默认 `main stop` → `main start`，而 `main stop` 会 `stop_refresh_daemon` —— 若由 `refresh_sub` 自己调用，等于自杀（请求/循环被半路终止）。所以刷新链路里用 `MIPROXY_NO_RESTART=1` 让 `config_callback` 只重生成配置，由 `refresh_sub` 自己 `restart_mihomo`。新增任何“脚本内调用 `config_callback`”的场景，都要先想清楚是否会杀掉调用方。
9. **`config_callback` 依赖 `wizard_*` 环境变量**（向导/管理服务下发）。一个都没有时（用户手动执行排障），脚本会回退读取 `settings.conf`；否则会把订阅、端口整体写回默认值。
10. 读 `settings.conf` 取值一律 `| tr -d '\r\n'`（不要只删 `\n`）：CRLF 行尾会残留 `\r`，让 `[ "$x" = "remote" ]` 静默失效、端口值带不可见字符。

---

## 6. 生命周期与脚本职责

| 动作 | 入口脚本 | 职责 |
|------|----------|------|
| 安装前 | `install_init` | 当前空（exit 0） |
| 安装后 | `install_callback` | 拷资源 → 下订阅 → 生成 config.yaml → 写 settings.conf → 调 `main start` |
| 配置前 | `config_init` | 当前空 |
| 配置后 | `config_callback` | 重新生成配置并重启（**未读，改配置相关逻辑时必须先读它**） |
| 启动/停止/重启/状态 | `main` | 管理 mihomo 进程 + 刷新守护进程 + 管理服务（`admin_server.py`）；`status` 返回 fnOS 约定的退出码（运行=0，停止=3）。**即使 mihomo 启动失败也会拉起管理服务**，便于在界面里改错配置 |
| 订阅刷新 | `refresh_sub` | 常驻守护进程，按 `wizard_refresh_interval` 小时循环；自带 `LOCK_FILE` 防并发；结束时 `trap` 清理锁与 PID 文件。**每轮都走「下载缓存 → `apply_config no-restart` 重生成 config.yaml → `restart_mihomo`」**，成功记「已应用新订阅」。带 `once` 参数时为单次「刷新并应用」（下载 → `config_callback` 重生成 → 重启内核），供管理界面「立即刷新订阅」调用 |
| 管理服务 | `admin_server.py` | 由 `main` 拉起（`nohup`），提供 9091 的静态页 + REST API；不受 mihomo 启停影响 |
| 升级 | `upgrade_init` / `upgrade_callback` | 升级兼容（日志写入 `upgrade_init.log` / `upgrade_callback.log`） |
| 卸载 | `uninstall_init` / `uninstall_callback` | 清理（含停止管理服务、删 `admin.pid`） |

**进程/锁文件约定**（都在 `TRIM_PKGVAR` 下）：
- `app.pid` — mihomo 主进程 PID
- `refresh_sub.pid` — 刷新守护进程 PID
- `refresh_sub.lock` — 刷新并发锁
- `admin.pid` — 管理服务 PID
- `proxy_providers/subscription.yaml` — 当前生效订阅（缓存）

**日志文件**：`info.log`（mihomo + main）、`refresh_sub.log`、`admin.log`（管理服务请求日志）、`install_callback.log`、`upgrade_init.log`、`upgrade_callback.log`。v2.7 已做日志分离，新增日志请归类写入，避免混写。

---

## 7. 管理界面（端口 9091）

前端 `app/ui/admin/index.html`（单文件，原生 JS，无构建），后端 `cmd/admin_server.py`（Python3 标准库，**不引入任何第三方依赖**，因为 fnOS 上只保证有 python3）。

**页面结构**：单文件内用 **hash 路由**分 5 个视图（`#/overview`、`#/subscriptions`、`#/proxy`、`#/kernel`、`#/logs`），顶部 sticky 菜单切换，`<section class="view" id="view-xxx">` 靠 `.active` 类显示/隐藏，不会真的跳页（保持登录态与状态，刷新/收藏/前进后退都可用）。新增页面：①在 `#views` 里加 section ②在 `#navTabs` 加 `<a href="#/xxx" data-view="xxx">` ③在 `VIEW_LOADERS` 里加同名键（决定进入时拉哪些数据）。注意：**每个视图只加载自己需要的数据**，日志自动刷新定时器必须在离开日志页时 `stopAutoLog()`，否则后台空转。

**部署路径**：`install_callback` 会把 `app/ui/admin/` 拷到 `${TRIM_PKGVAR}/ui/admin/`；服务端按 `VAR_DIR/ui/admin` → `APP_DEST/ui/admin` 顺序找。

**接口**（除静态资源外均需鉴权）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` `/admin/` `/admin/*` | 静态页（`_serve_static`，含路径穿越防护） |
| GET | `/api/status` | 内核/管理服务 PID 与存活、`config.yaml` 与订阅缓存大小时间、`auth_required` |
| GET | `/api/settings` | 当前配置；**密码/密钥只返回 `proxy_pass_set` / `secret_set` 布尔值** |
| POST | `/api/settings` | 校验 → 以 `wizard_*` 环境变量调 `config_callback` → 返回 rc、内核状态、`info.log` 尾部。请求带 `active_sub_id` 时会按清单校准 `sub_*`，防止前端旧状态覆盖订阅 |
| GET | `/api/subscriptions` | 订阅清单 + `active_id`（以 settings.conf 反查；向导里配的订阅不在清单时会合成 `legacy` 条目） |
| POST | `/api/subscriptions` | 保存清单；**仅当启用项变化**才调 `config_callback` 重生成配置并重启（`applied` 字段告诉前端） |
| GET | `/api/logs?name=&lines=` | 日志尾部，`name` 必须在 `LOG_WHITELIST` 白名单内 |
| POST | `/api/refresh` | 调 `refresh_sub once`（刷新并应用） |
| GET | `/api/kernel` | 当前/内置内核版本、架构、备份列表、缓存的最新版本、`kernel.json` 里的镜像前缀、当前任务快照 |
| POST | `/api/kernel/check` | 拉 `MetaCubeX/mihomo` Releases（镜像优先、直连兜底），按本机架构挑资产后缓存进 `kernel.json` |
| POST | `/api/kernel/install` | 开后台任务：`{source: github\|file\|bundled, version?, asset?, path?}` → 202；已存在运行中任务时 409 |
| GET | `/api/kernel/task` | 任务进度（`state/phase/percent/message/error/log`），前端 1.2s 轮询到非 running 为止 |
| POST | `/api/kernel/rollback` | 用 `kernel_backup/` 里的某个备份覆盖当前内核（缺省取最近一个） |
| POST | `/api/kernel/config` | 保存下载镜像前缀（只对 `github.com` / `api.github.com` 等官方域名生效） |

**鉴权模型**：令牌 = `settings.conf` 的 `wizard_secret`（缺失则用 `wizard_admin_token`）；两者都空则**开放访问**（局域网）。请求头 `X-Admin-Token` 或 `Authorization: Bearer`。前端把令牌存 `localStorage`。

**必须遵守的四条实现约定**：
1. **`MIPROXY_KEEP_ADMIN=1`**：管理服务下发配置时会触发 `config_callback` → `main stop/start`。若不停用该开关，`stop_admin` 会把管理服务自己杀掉，导致请求中断、前端永远等不到响应。所有由管理服务派生的 `config_callback` / `refresh_sub` 调用都必须带这个环境变量。
2. **敏感字段留空 = 保持不变**：`proxy_pass` / `secret` 为空且未置 `clear_*` 时，从 `settings.conf` 读旧值回填；置 `clear_*` 才清空。前端占位符文案要与之一致。
3. **`settings.conf` 是整体重写**：`config_callback` 用 `> settings.conf` 全量覆盖，键不在白名单里就会丢（历史 bug：`wizard_admin_port` 被抹掉）。新增持久化键必须同步加进重写块，或改成读写合并。
4. **单一启用**：订阅可以多条，但 `settings.conf` 的 `wizard_sub_*` 只能有一套。切换启用 = 把目标项写进 `wizard_sub_*` 再 `apply`；不要把「多个订阅的节点合并」偷懒实现成同时写多份缓存，否则 `refresh_sub` / `main` 的假设全部失效。
5. **内核操作必须可回滚**：所有换内核的路径都走 `_install_binary()`（校验 `-v` → 备份 → 原子 `os.replace` → `main restart` → 失败回滚），不要另写一套「直接覆盖 + 重启」的捷径，否则失败后设备会没有可用内核。

**本地调试**（Windows 也可）：`TRIM_PKGVAR` / `TRIM_PKGETC` / `TRIM_APPDEST` 指向临时目录 + `ADMIN_PORT=19091` 启动即可；`bash` 定位见 §9.8。内核页要脱离外网验证时，用 `MIPROXY_KERNEL_API`（Releases 接口基址）与 `MIPROXY_KERNEL_BASE`（下载基址）指向本地 mock 服务；Windows 上内核二进制是 ELF/脚本，`_run_kernel()` 会自动改用 `bash` 执行 `-v`。

**一键入口**：`bash scripts/dev-local.sh init|admin|status|sync-ui|restart|stop|clean`（自动完成上面那套环境变量与目录准备，默认开发目录 `/tmp/miproxy-dev`，可用 `MIPROXY_DEV_ROOT` 覆盖）。改完 `app/ui/**` 记得 `sync-ui`，否则管理页读的还是 `$TRIM_PKGVAR/ui/admin` 里的旧副本。

---

## 8. 本地开发与打包

- **无自动化构建**。调试 bash 脚本：`bash scripts/dev-local.sh init` 造好目录后直接跑 `cmd/*`，或参照 README「本地开发」手动注入 §3 的环境变量。
- 打包 `.fpk`：`fnpack build --directory .`（整目录打包，新增的 `cmd/` 文件与 `app/ui/admin/` 会**自动**进包）；也可按 fnOS 规范手打（manifest + app + cmd + config + wizard + 图标）。注意 `scripts/` 也会被打进去，CI 里已 `rm -rf scripts`，本地打包同样要删。
- **CI**：`.github/workflows/build-release.yml` 里有一段给脚本加 `chmod +x` 并转 LF 的循环（x86 / arm 各一份），**新增可执行脚本必须加进这两处清单**，否则打出来的包里脚本不可执行（Windows 上开发尤其容易漏）。
- **不要**在仓库里提交大体积二进制（mihomo / 前端 / mmdb）的频繁变更，除非确实是内核/前端升级——这类变更应通过“自动同步二进制文件”流程处理。

---

## 9. 已知坑与待办（改动前请留意）

1. **大小写冲突（已存在）**：`app/ui/images/` 同时有 `ICON_256.PNG` 与 `icon_256.png`，在 Windows（大小写不敏感）检出会互相覆盖，导致 `git status` 显示 `ICON_256.PNG` 为已修改。**不要在 Windows 上提交这个“修改”**，更不要在大小写文件系统上误删其一；根治需在源头把文件名统一。
2. **版本字符串漂移**：`wizard/config` 的“关于”步骤已同步到 `v2.8.0`，但 `cmd/install_callback` 头部注释仍写 “sync v2.6.38”（脚本内部的 v9/v11 是自身迭代标记，不是产品版本，别混改）。改动时以 `manifest` 的 `version` 为准，并顺手同步这些文案。
3. **README 引用了 `LICENSE`**，但仓库根目录实际**没有 LICENSE 文件**——若需合规发布应补上。
4. **强依赖 `python3`**：`extract_yaml_block` 用 python3 解析订阅 YAML。fnOS 环境须自带 python3，否则安装会静默失败（heredoc 带 `2>/dev/null`）。
5. **root 权限**：`config/privilege` 声明 `run-as: root`，TUN 必需；改权限相关要确认目标环境。
6. 脚本里有大量 `find_script` / 多路径兜底（如 `/var/apps/miproxy/cmd/`、`/var/packages/miproxy/cmd/`），是为了兼容不同 fnOS 安装布局——新增脚本调用尽量复用这些路径探测逻辑，不要硬编码单一路径。
7. **`config.yaml` 生成逻辑有两份副本**（`install_callback` / `config_callback`），改动必须成对进行（见 §5）。同样 `extract_yaml_block` 也有两份，历史 bug 正是「只修了一份」导致的。
8. **本地（Windows）调试 python 服务时 `bash` 可能指错**：PATH 里的 `bash` 常是 `C:\Windows\System32\bash.exe`（WSL 启动器），在安全策略下会直接被拦截并返回 rc=1，现象是「配置保存成功但 config.yaml 没更新」。`admin_server.py` 的 `bash_bin()` 会优先 `/bin/bash`，可用环境变量 `MIPROXY_BASH` 指定；临时验证也可把 Git Bash 目录放到 PATH 前面。
9. **Windows 下 `os.kill(pid, 0)` 不是「探测存活」**，而是 TerminateProcess（会直接杀进程）。判断进程是否存活用 `tasklist`（`admin_server.pid_alive()` 已处理），bash 侧用 `kill -0`。
10. **「未启用订阅」不能再吃旧缓存**：两个 callback 抽取订阅字段前必须判断 `SUB_TYPE` 属于 `remote`/`local`（已修）。`proxy_providers/subscription.yaml` 会一直留着上一次的缓存以便快速切回，如果不加判断，`sub_type=none` 时照样会把旧节点写进 `config.yaml`，表现为「停用了但还在走代理」。
11. **「只重启内核」不等于「配置生效」**：内核每次启动都从磁盘读 `config.yaml`，而节点是内嵌的，所以「换缓存 + 重启」重启的仍是旧配置。`refresh_sub` 的定时路径过去正是如此（已修）；判断某条链路是否真生效，只需看 `config.yaml` 内容是否变了 + 内核 PID 是否换了。
12. **`main stop` 是「杀全族」**：它会 `stop_refresh_daemon` + `stop_admin`。所以任何「由守护进程/管理服务自己触发配置应用」的路径，都必须用 `MIPROXY_NO_RESTART=1`（刷新链路）或 `MIPROXY_KEEP_ADMIN=1`（管理链路）避免自杀。
13. **内核操作的存活判定走 `main status`**，不要自己读 `app.pid` 再 `pid_alive()`：pid 文件的语义由 `main` 维护（Linux 是真实 pid，Windows 本地调试时是 MSYS pid），两套命名空间混用会得到「明明起来了却判定失败」的假故障（本轮踩过，导致升级后误判失败并回滚）。
14. **`install_callback` 不再无条件覆盖内核**：内置内核始终另存 `target/mihomo.bundled`，只在 `target/mihomo` 缺失时补齐。否则用户手动升级的内核会在升级 `.fpk` 后被悄悄换回去。
15. **下载地址不接受任意 URL**：`/api/kernel/install` 只接受 `version`（拼 `KERNEL_DL_BASE`）、`file`（本机路径）或 `bundled`；资产名还要过 `^mihomo-linux-` 正则。镜像前缀只对 `MIRROR_HOSTS` 里的官方域名拼接（`https://ghfast.top/` 这一类服务认的是原始 GitHub URL），给自建/本地地址套前缀只会拼出 404。
16. **无面板密钥时写操作一律拒绝**：`do_POST` 里 `_write_blocked()` 会对所有 POST 返回 403（`need_token: true`），前端据此弹引导。**新增任何 POST 路由都必须放在这两个检查之后**，否则等于开了个未认证入口——无密钥 + `/api/kernel/install` + 任意 `mirror` 就是一条完整的远程代码执行链。
17. **`settings.conf` 的读取必须同时裁掉 `\r` 和 `\n`**（`tr -d '\r\n'`）：只删 `\n` 时，CRLF 行尾会让 `sub_type` 变成 `remote\r`，比较失败，表现为「设了远程订阅却不刷新」。
18. **`refresh_sub` 的三个自我保护**：锁文件要写 PID 并支持残留清理（`lock_is_stale`，否则一次异常退出会让自动刷新**永久**跳过）；`get_interval_seconds` 有 60 秒下限（否则 `sleep 0` 让守护进程满载空转）；`refresh_sub` 复制到数据目录后要**每次比对同步**（只在缺失时复制的话，升级 `.fpk` 后旧副本会一直生效）。
19. **卸载链路两个红线**：`uninstall_init` 只能用 `pkill -f "${TRIM_PKGVAR}/target/mihomo"`（`pkill -f mihomo` 会误杀同机其它代理应用的进程）；`uninstall_callback` 的 `rm -rf` 必须过 `is_app_dir()`（路径含 `miproxy`）白名单，防止 `TRIM_*` 被误设成卷根目录时删空整卷。
20. **本仓库是 fork，推 tag 不会触发构建**：`fengmuxi/miproxy-fpk` fork 自 `56025192/miproxy-fpk`。GitHub 默认不在 fork 上跑工作流，现象是 tag 推上去了、`git ls-remote --tags` 看得见，但 `GET /actions/runs` 的 `total_count` 恒为 0（父仓库同期有 169 次 push 触发的 Build FPK，可作对照），而 `workflow_dispatch` 仍然能跑。发布要么先去 Actions 页点 "I understand my workflows, go ahead and enable them"，要么用 `POST /actions/workflows/build-release.yml/dispatches` 手动触发（`inputs` 必须是对象，空也要给 `{}`）。
21. **用 API 触发时若 tag 已指向 HEAD，Release 正文是空的**：`changes` 作业用 `git describe --tags --abbrev=0` 取上一个 tag，正好取到刚推的这个 tag，`git diff` 为空 → 「变更详情」只剩两行表头。要么先触发后打 tag，要么事后 `PATCH /releases/{id}` 把正文补上。
22. **本机 `~/.gitconfig` 里有一行空的 `credential.helper=`**，它会把系统级的 `manager` 清空，导致所有 `git push` 报 `could not read Username for 'https://github.com': terminal prompts disabled`（明明 Windows 凭据管理器里就有 `git:https://github.com`）。不要改用户的全局配置，用命令级覆盖：`git -c credential.helper=manager push ...`；需要 token 调 API 时同理 `git -c credential.helper=manager credential fill`。
23. **顶部菜单切页不要依赖 hash**：fnOS 桌面窗口是壳程序里的 webview，`<a href="#/x">` 这次同文档导航可能被壳拦掉（`will-navigate` / `did-start-navigation` 里 `preventDefault`），于是 hash 不变 → `hashchange` 不触发 → 菜单点了毫无反应，而页面其它 JS 一切正常（概览数据照常渲染，很能骗人）。现在的做法：`_view` 内存态是唯一真相，`showView(name, syncHash)` 先切 DOM 再尽力同步 hash，点击监听挂 `document` 捕获阶段；**`route()` 只重渲染 `_view`，绝不能回读 hash**，否则受限环境里点「连接」会被旧 hash 弹回概览。
24. **`/api/status` 的 `panel_port`** 来自 `settings.conf` 的 `wizard_api_port`（mihomo 控制端口，面板页 `external-ui` 挂在它的 `/ui/`）。新加需要跨端口跳转的入口时复用它，别再往 HTML 里写死 9090/9091。

---

## 10. 安全红线

- 凡涉及外部输入（订阅 URL、用户名、密钥、本地路径）的拼接，沿用现有转义/白名单做法（如 §5.4 的域名提取），**禁止**直接把 `wizard_*` 变量拼进 `eval`/命令字符串，防命令注入。
- `secret`、`wizard_proxy_pass` 等敏感字段只写进 `settings.conf`（仅本机 root 可读），不要回显到日志或前端（管理接口只返回 `*_set` 布尔值）。
- 管理服务（9091）监听 `0.0.0.0`：无密钥时**只读放行、写操作全拒**（`_write_blocked()`）。新增接口必须走 `_authorized()`，静态资源必须保留 `_serve_static` 的路径穿越校验，日志读取必须限制在 `LOG_WHITELIST`。
- 不要提交真实订阅 URL、密钥、凭据到仓库。
