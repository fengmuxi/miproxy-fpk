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
│                            #   frame-zashboard.html / frame-metacubexd.html = 带顶栏的包装页，用 iframe 承载两个第三方面板（见 §9.31）
├── cmd/                     # ★ 主要“源码”：fnOS 生命周期 bash 脚本
│   ├── main                 # 启停管理：start|stop|restart|status（含刷新守护进程 + 管理服务）
│   ├── refresh_sub          # 订阅刷新守护进程（循环下载订阅 + 重启 mihomo）；`once` 为单次“刷新并应用”
│   ├── admin_server.py      # 管理界面后端（Python3 标准库，端口 9091，见 §7）
│   ├── sub_download.sh      # 订阅下载统一入口（带 clash.meta UA + 换 UA 重试，见 §9.33）
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

`config.yaml` **完全由脚本用 `echo` 拼接生成**，不是手写文件。生成核心已统一抽到共用库 **`cmd/miproxy_config_lib.sh`**（两个 callback 在顶部 `source` 它），字段抽取（`extract_yaml_block` / `extract_subscription_fields`）、节点计数（`count_yaml_nodes`）、订阅诊断（`diagnose_subscription` / `config_has_nodes`）、域名过滤（`compute_domain_filter`）、最终生成（`emit_config_yaml`）都只有这一份实现，**不会再出现「只修了一份」的分歧**。两个 callback 只保留各自差异化流程（下载订阅 / 写 settings.conf / 重启内核 / 进度上报 / 退出码）。关键点：

1. **生成入口唯一**：改 config.yaml 模板（字段列表、TUN 参数、DNS 兜底、fake-ip-filter 等）只改 `miproxy_config_lib.sh` 的 `emit_config_yaml`，install 与 config 两条链路自动一致。验证方式仍可用「同一份订阅分别跑两个脚本，再 diff 生成的 config.yaml」（应完全一致）。
2. **订阅解析**用一段内嵌的 `python3` heredoc `extract_yaml_block(key, file)`，按“顶层键”从订阅源 YAML 抽取整块（proxies / proxy-groups / rules / dns / proxy-providers 等十几个字段）。抽取后**必须过一遍 `normalize()` 统一缩进**（见 §9.36）：订阅源常见的「顶格 `-`」写法要缩进到父键下面，输出的 `config.yaml` 才是规范 YAML。其余内容保持订阅源原始格式。
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
| 配置后 | `config_callback` | 重新生成配置 + 用 `restart-kernel` **只重启内核**（**未读，改配置相关逻辑时必须先读它**）；重启期间写 `apply.progress` 阶段进度，并用 `RESTART_FLAG` 让 `main status` 仍返回运行，避免 fnOS 误判应用停止 |
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

**页面结构**：单文件内用 **hash 路由**分 6 个视图（`#/overview`、`#/subscriptions`、`#/proxy`、`#/nodes`、`#/kernel`、`#/logs`），顶部 sticky 菜单切换（菜单最左的「← 面板」是跳转到 mihomo 面板的外链，不是视图），`<section class="view" id="view-xxx">` 靠 `.active` 类显示/隐藏，不会真的跳页（保持登录态与状态，刷新/收藏/前进后退都可用）。新增页面：①在 `#views` 里加 section ②在 `#navTabs` 加 `<a class="tab" href="#/xxx" data-view="xxx">` ③在 `VIEW_LOADERS` 里加同名键（决定进入时拉哪些数据）。注意：**每个视图只加载自己需要的数据**，日志自动刷新定时器必须在离开日志页时 `stopAutoLog()`，否则后台空转。

**部署路径**：`install_callback` 会把 `app/ui/admin/` 拷到 `${TRIM_PKGVAR}/ui/admin/`；服务端按 `VAR_DIR/ui/admin` → `APP_DEST/ui/admin` 顺序找。

**接口**（除静态资源外均需鉴权）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` `/admin/` `/admin/*` | 静态页（`_serve_static`，含路径穿越防护） |
| GET | `/api/status` | 内核/管理服务 PID 与存活、`config.yaml` 与订阅缓存大小时间、`auth_required` |
| GET | `/api/settings` | 当前配置；**密码/密钥只返回 `proxy_pass_set` / `secret_set` 布尔值** |
| POST | `/api/settings` | 校验 → 以 `wizard_*` 环境变量**异步**调 `config_callback`（后台线程，返回 **202**）→ 前端轮询 `/api/apply/progress` 直到 `done`/`failed`；最终返回 rc、内核状态、`info.log` 尾部。请求带 `active_sub_id` 时会按清单校准 `sub_*`，防止前端旧状态覆盖订阅。**rc=3** 内核重启失败、**rc=4** 订阅没解析出节点（带诊断原文）、**rc=5** 有其它配置应用正在进行（`apply.lock` 冲突） |
| GET | `/api/subscriptions` | 订阅清单 + `active_id`（以 settings.conf 反查；向导里配的订阅不在清单时会合成 `legacy` 条目） |
| POST | `/api/subscriptions` | 保存清单；**仅当启用项变化**才异步调 `config_callback` 重生成配置并重启（`applied` 字段 + 202 异步 + `/api/apply/progress` 轮询） |
| GET | `/api/apply/progress` | 切换订阅 / 保存配置的阶段进度（`stage` / `percent` / `message`，UTF-8，由 `config_callback` 写入 `apply.progress`）：`downloading` → `parsing` → `generating` → `restarting` → `done` / `failed`。前端据此显示「下载订阅信息 → 解析中 → 生成配置中 → 重启内核中 → 已完成」并弹出**遮罩**阻止并发操作 |
| GET | `/api/nodes` | 解析 `config.yaml` 的 `proxies` 得节点清单（名称/类型/server/port/udp/延迟），并合并内核 `/proxies` 的 `history[-1].delay`；`kernel_running` 告知能否实测 |
| POST | `/api/nodes/delay` | 并发测速：`{names:[], url?, timeout?}` → `{results:{name:{delay,error}}}`。只读，**不走 `_write_blocked()`**；内核未运行返回 409 |
| GET | `/api/logs?name=&lines=` | 日志尾部，`name` 必须在 `LOG_WHITELIST` 白名单内 |
| POST | `/api/refresh` | 调 `refresh_sub once`（刷新并应用） |
| GET | `/api/kernel` | 当前/内置内核版本、架构、备份列表、缓存的最新版本、`kernel.json` 里的镜像前缀、当前任务快照 |
| POST | `/api/kernel/check` | 拉 `MetaCubeX/mihomo` Releases（镜像优先、直连兜底），按本机架构挑资产后缓存进 `kernel.json` |
| POST | `/api/kernel/install` | 开后台任务：`{source: github\|file\|bundled, version?, asset?, path?}` → 202；已存在运行中任务时 409 |
| GET | `/api/kernel/task` | 任务进度（`state/phase/percent/message/error/log`），前端 1.2s 轮询到非 running 为止 |
| POST | `/api/kernel/rollback` | 用 `kernel_backup/` 里的某个备份覆盖当前内核（缺省取最近一个） |
| POST | `/api/kernel/config` | 保存下载镜像前缀（只对 `github.com` / `api.github.com` 等官方域名生效） |

**鉴权模型**：令牌 = `settings.conf` 的 `wizard_secret`（缺失则用 `wizard_admin_token`）；两者都空则**开放访问**（局域网）。请求头 `X-Admin-Token` 或 `Authorization: Bearer`。前端把令牌存 `localStorage`。
注意区分两套密钥：**管理接口的令牌**来自 `settings.conf`，而**访问内核控制端口**（`/proxies`、`/proxies/{n}/delay`、`-v` 版本探测等）要用 `config.yaml` 里的 `secret`——见 §9.28。

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
2. **版本字符串漂移**：`wizard/config` 的“关于”步骤已同步到 `v2.8.5`，但 `cmd/install_callback` 头部注释仍写 “sync v2.6.38”（脚本内部的 v9/v11 是自身迭代标记，不是产品版本，别混改）。改动时以 `manifest` 的 `version` 为准，并顺手同步这些文案。
3. **README 引用了 `LICENSE`**，但仓库根目录实际**没有 LICENSE 文件**——若需合规发布应补上。
4. **强依赖 `python3`**：`extract_yaml_block` 用 python3 解析订阅 YAML。fnOS 环境须自带 python3，否则安装会静默失败（heredoc 带 `2>/dev/null`）。
5. **root 权限**：`config/privilege` 声明 `run-as: root`，TUN 必需；改权限相关要确认目标环境。
6. 脚本里有大量 `find_script` / 多路径兜底（如 `/var/apps/miproxy/cmd/`、`/var/packages/miproxy/cmd/`），是为了兼容不同 fnOS 安装布局——新增脚本调用尽量复用这些路径探测逻辑，不要硬编码单一路径。
7. **`config.yaml` 生成逻辑已统一到 `cmd/miproxy_config_lib.sh`**（v2.8.6）：`extract_yaml_block` / `count_yaml_nodes` / `diagnose_subscription` / `config_has_nodes` / `compute_domain_filter` / `extract_subscription_fields` / `emit_config_yaml` 只有这一份实现，两个 callback `source` 它调用。新增脚本要放到 `main` 的 `sync_cmd_scripts()` 列表与 CI chmod 循环里（这文件不含 shebang 执行路径，但保持列表完整便于排查）。
8. **本地（Windows）调试 python 服务时 `bash` 可能指错**：PATH 里的 `bash` 常是 `C:\Windows\System32\bash.exe`（WSL 启动器），在安全策略下会直接被拦截并返回 rc=1，现象是「配置保存成功但 config.yaml 没更新」。`admin_server.py` 的 `bash_bin()` 会优先 `/bin/bash`，可用环境变量 `MIPROXY_BASH` 指定；临时验证也可把 Git Bash 目录放到 PATH 前面。
9. **Windows 下 `os.kill(pid, 0)` 不是「探测存活」**，而是 TerminateProcess（会直接杀进程）。判断进程是否存活用 `tasklist`（`admin_server.pid_alive()` 已处理），bash 侧用 `kill -0`。
10. **「未启用订阅」不能再吃旧缓存**：两个 callback 抽取订阅字段前必须判断 `SUB_TYPE` 属于 `remote`/`local`（已修）。`proxy_providers/subscription.yaml` 会一直留着上一次的缓存以便快速切回，如果不加判断，`sub_type=none` 时照样会把旧节点写进 `config.yaml`，表现为「停用了但还在走代理」。
11. **「只重启内核」不等于「配置生效」**：内核每次启动都从磁盘读 `config.yaml`，而节点是内嵌的，所以「换缓存 + 重启」重启的仍是旧配置。`refresh_sub` 的定时路径过去正是如此（已修）；判断某条链路是否真生效，只需看 `config.yaml` 内容是否变了 + 内核 PID 是否换了。
12. **`main stop` 是「杀全族」**：它会 `stop_refresh_daemon` + `stop_admin`。所以任何「由守护进程/管理服务自己触发配置应用」的路径，都必须用 `MIPROXY_NO_RESTART=1`（刷新链路）或 `MIPROXY_KEEP_ADMIN=1`（管理链路）避免自杀。
13. **「沿用旧缓存」只对 URL 未变成立；切换订阅（URL 变更）下载失败必须整体回滚**（v2.8.7，exit 6）：否则 config.yaml 用旧缓存节点、settings.conf 却写成新 URL，界面显示新订阅、实际跑旧订阅——「显示与生效不一致」比失败更坑。回滚 = 不覆盖 config.yaml、不重写 settings.conf、不重启内核。URL 未变时的下载失败仍可沿用旧缓存（定时刷新链路：refresh_sub 自己 fetch 失败就不会调 config_callback）。
14. **`sub_download.sh` 的 rc=2 路径曾覆盖旧缓存**（v2.8.7 已修）：下到的内容无法识别时，先试 v2ray 转换（`sub_convert.py`），仍失败才把无效内容另存 `subscription.yaml.invalid`，绝不能 `mv` 到缓存路径——一次坏响应就能毁掉用户仅有的可用节点缓存。
15. **v2ray/链接列表订阅在下载入口统一转换**（v2.8.7，`cmd/sub_convert.py`）：`sub_download.sh` 拿到非 Clash 内容时先尝试转换（base64 或明文的 vmess/vless/ss/trojan/ssr 链接列表 → Clash YAML，纯 python3 标准库、无 PyYAML 依赖），成功即正常返回 0，下游（install/config/refresh）零改动。转换脚本要跟 `sub_download.sh` 一起进 `main` 的 `sync_cmd_scripts()` 与 CI chmod 列表；kcp/quic 等不支持的传输层按节点跳过，全部失败才返回 2 走回滚。
13. **内核操作的存活判定走 `main status`**，不要自己读 `app.pid` 再 `pid_alive()`：pid 文件的语义由 `main` 维护（Linux 是真实 pid，Windows 本地调试时是 MSYS pid），两套命名空间混用会得到「明明起来了却判定失败」的假故障（本轮踩过，导致升级后误判失败并回滚）。
14. **`install_callback` 不再无条件覆盖内核**：内置内核始终另存 `target/mihomo.bundled`，只在 `target/mihomo` 缺失时补齐。否则用户手动升级的内核会在升级 `.fpk` 后被悄悄换回去。
15. **下载地址不接受任意 URL**：`/api/kernel/install` 只接受 `version`（拼 `KERNEL_DL_BASE`）、`file`（本机路径）或 `bundled`；资产名还要过 `^mihomo-linux-` 正则。镜像前缀只对 `MIRROR_HOSTS` 里的官方域名拼接（`https://ghfast.top/` 这一类服务认的是原始 GitHub URL），给自建/本地地址套前缀只会拼出 404。
16. **无面板密钥时写操作一律拒绝**：`do_POST` 里 `_write_blocked()` 会对所有 POST 返回 403（`need_token: true`），前端据此弹引导。**新增任何 POST 路由都必须放在这两个检查之后**，否则等于开了个未认证入口——无密钥 + `/api/kernel/install` + 任意 `mirror` 就是一条完整的远程代码执行链。唯一的例外是**明确只读**的 POST（当前只有 `/api/nodes/delay`：它只调内核的 delay 接口，不写配置、不动进程），这类可以放在 `_write_blocked()` 之前放行——毕竟刚装好还没填密钥时，用户连「节点通不通」都验证不了就没法用；但它**仍必须在 `_authorized()` 之后**，设了密钥就得验。
17. **`settings.conf` 的读取必须同时裁掉 `\r` 和 `\n`**（`tr -d '\r\n'`）：只删 `\n` 时，CRLF 行尾会让 `sub_type` 变成 `remote\r`，比较失败，表现为「设了远程订阅却不刷新」。
18. **`refresh_sub` 的三个自我保护**：锁文件要写 PID 并支持残留清理（`lock_is_stale`，否则一次异常退出会让自动刷新**永久**跳过）；`get_interval_seconds` 有 60 秒下限（否则 `sleep 0` 让守护进程满载空转）；`refresh_sub` 复制到数据目录后要**每次比对同步**（只在缺失时复制的话，升级 `.fpk` 后旧副本会一直生效）。
19. **卸载链路两个红线**：`uninstall_init` 只能用 `pkill -f "${TRIM_PKGVAR}/target/mihomo"`（`pkill -f mihomo` 会误杀同机其它代理应用的进程）；`uninstall_callback` 的 `rm -rf` 必须过 `is_app_dir()`（路径含 `miproxy`）白名单，防止 `TRIM_*` 被误设成卷根目录时删空整卷。
20. **本仓库是 fork，推 tag 不会触发构建**：`fengmuxi/miproxy-fpk` fork 自 `56025192/miproxy-fpk`。GitHub 默认不在 fork 上跑工作流，现象是 tag 推上去了、`git ls-remote --tags` 看得见，但 `GET /actions/runs` 的 `total_count` 恒为 0（父仓库同期有 169 次 push 触发的 Build FPK，可作对照），而 `workflow_dispatch` 仍然能跑。发布要么先去 Actions 页点 "I understand my workflows, go ahead and enable them"，要么用 `POST /actions/workflows/build-release.yml/dispatches` 手动触发（`inputs` 必须是对象，空也要给 `{}`）。
21. **用 API 触发时若 tag 已指向 HEAD，Release 正文是空的**：`changes` 作业用 `git describe --tags --abbrev=0` 取上一个 tag，正好取到刚推的这个 tag，`git diff` 为空 → 「变更详情」只剩两行表头。要么先触发后打 tag，要么事后 `PATCH /releases/{id}` 把正文补上。
22. **本机 `~/.gitconfig` 里有一行空的 `credential.helper=`**，它会把系统级的 `manager` 清空，导致所有 `git push` 报 `could not read Username for 'https://github.com': terminal prompts disabled`（明明 Windows 凭据管理器里就有 `git:https://github.com`）。不要改用户的全局配置，用命令级覆盖：`git -c credential.helper=manager push ...`；需要 token 调 API 时同理 `git -c credential.helper=manager credential fill`。
23. **顶部菜单切页不要依赖 hash**：fnOS 桌面窗口是壳程序里的 webview，`<a href="#/x">` 这次同文档导航可能被壳拦掉（`will-navigate` / `did-start-navigation` 里 `preventDefault`），于是 hash 不变 → `hashchange` 不触发 → 菜单点了毫无反应，而页面其它 JS 一切正常（概览数据照常渲染，很能骗人）。现在的做法：`_view` 内存态是唯一真相，`showView(name, syncHash)` 先切 DOM 再尽力同步 hash，点击监听挂 `document` 捕获阶段；**`route()` 只重渲染 `_view`，绝不能回读 hash**，否则受限环境里点「连接」会被旧 hash 弹回概览。
24. **`/api/status` 的 `panel_port`** 来自 `settings.conf` 的 `wizard_api_port`（mihomo 控制端口，面板页 `external-ui` 挂在它的 `/ui/`）。新加需要跨端口跳转的入口时复用它，别再往 HTML 里写死 9090/9091。
25. **`config_callback` 的退出码是「是否真的生效」的唯一凭据**：它过去不管内核重启成败都 `exit 0`，于是「配置已保存」和「配置没生效」在接口层长得一模一样——这就是「界面上启用了订阅、config.yaml 却没生效」的根。现在内核重启失败会返回 **3**，`_apply_settings_locked` 据此返回 500 并附 `config_updated` / `kernel_restarted` / `kernel_pid_before|after` 三个事实。**新增任何调用 config_callback 的路径，都必须检查返回码，不能只看有没有报错输出。** 另外 `MIPROXY_NO_RESTART=1`（refresh_sub 定时刷新用）仍然 `exit 0`，因为重启由调用方负责。
26. **前端判断「应用成功」不能只看 HTTP 状态**：`applyVerdict()` 会依次看 `ok` → `config_updated` → `kernel_restarted`。只改 DOM 提示而不核对这三项，就会出现「页面说已启用了、节点其实一个都没进去」的假成功。
27. **节点清单要解析 `config.yaml`，不要读订阅缓存**：缓存里有节点 ≠ 内核加载了节点（节点是内嵌进 `config.yaml` 的，见 §9.11）。节点页若图省事去读 `proxy_providers/subscription.yaml`，「订阅启用了但配置没生效」这个最常见的故障就会被它掩盖成「一切正常」。同理，延迟的初值只能来自内核 `history`（可能很旧），页面上要说清「测速才是现测」，否则用户会拿几天前的数字判断节点死活。
28. **访问内核控制端口用 `config.yaml` 的 `secret`，不是 `wizard_secret`**：`wizard_secret` 是「期望值」，`config.yaml` 的 `secret` 是「内核实际生效值」。用户改了面板密钥但没重新应用配置时两者不一致，按设置项去请求必然 401 → 节点页整页 `unauthorized`，看起来像内核坏了。`_mihomo_secret()` 因此优先读 `config.yaml`，读不到才退回设置项。另外调内核 API 时必须 `ProxyHandler({})` 显式禁掉环境变量里的 `http_proxy`：目标是 `127.0.0.1`，走代理只会绕一圈失败；测速超时内核返回 **408** 且 body 里有 `{"message":...}`，这是有效信息，别当异常吞掉。
29. **并发测速要设上限**：`test_nodes()` 用 `ThreadPoolExecutor(max_workers=6)`，单个 4 秒超时，单批最多 300 个。要是不限并发，一个 500 节点的订阅点「全部测速」会瞬间打满文件描述符和内核连接数，连正常上网都受影响。单点失败只影响该节点（`results[name].error`），不能让一个超时把整批拖垮。
30. **`__file__` 不可信：脚本可能正从「装完就会被删的临时目录」里运行**。fnOS 安装/升级的链路是 `/vol1/appcenter-downloads/<包名>`（解包临时目录）→ 执行 `install_callback` → 拉起 `main` → 拉起 `admin_server.py`。装完临时目录被系统清理，进程却还活着（代码已在内存、静态页从数据目录读），于是 `admin_server.py` 里 `os.path.dirname(__file__)` 指向了一个已消失的路径，**所有 `os.path.isfile(CMD_DIR/xxx)` 全部失败**。现象极具迷惑性：页面、状态、订阅清单都正常，一点保存就报「找不到 config_callback 脚本」，而更早的版本还会把它当成「保存成功但没生效」。现在有两道防线：①`main` 的 `sync_cmd_scripts()` 每次启动把 `main` / `admin_server.py` / `config_callback` / `refresh_sub` 按内容比对同步到 `${TRIM_PKGVAR}/cmd/`，`find_admin_script()` 优先从那里启动；②`admin_server.find_cmd_script()` 按「数据目录 → 安装目录 → `/var/apps` → `/var/packages` → `__file__`」依次探测，报错时把查过的目录全部列出。**新增任何「按脚本自身位置找兄弟脚本」的代码，都不要只写 `os.path.join(os.path.dirname(__file__), ...)`。** 判据是「这个路径现在还能用吗」；「它是不是我的邻居」并不构成理由。
31. **不要改第三方面板的文件，要用「包装页 + iframe」承载**。Zashboard / MetaCubeXD 是上游前端产物：改 `app/ui/<panel>/index.html` 会在面板升级时被整体覆盖，把仓库搞脏；往里注入元素也会**挡住面板自己的操作元素**（面板里大量用 `position: fixed` 的顶栏/侧栏元素，注入的悬浮按钮正好压在侧栏图标上；用户已经明确要求改成顶部导航行）。现在的做法：`app/ui/frame-zashboard.html`、`app/ui/frame-metacubexd.html` 两个自包含的包装页，顶栏用 flex 固定 32px、`iframe` 撑满剩余高度（`flex:1 1 auto`），面板在 iframe 里按自己的视口渲染 —— 顶栏与面板零重叠，且完全不碰第三方文件。安装时把这两个文件拷到 `$TRIM_PKGVAR/ui/`（`install_callback` 里的 `_frame` 循环），选择页的入口指向它们。
32. **`config_callback` 必须自己 `export MIPROXY_KEEP_ADMIN=1`**。管理服务调 `config_callback` 时会在环境里带这个标记，但 **fnOS 向导触发的 `config_callback` 不会带** —— `config_callback` 内部要重启内核，若走「全家桶」重启（`main stop`/`restart`）会把管理服务一起停掉，于是用户正开着的页面当场断线（表现为「应用崩了、要手动启动」）。内核重启与管理服务本就是两个独立进程，改配置不该牵连管理服务，所以这个标记要由 `config_callback` 自己无条件设置，不能指望调用方。同时 `config_callback` 现在用 **`main restart-kernel`**（只重启内核 + 刷新守护进程，不动管理服务），更彻底地解耦。
33. **下载订阅必须带 `User-Agent: clash.meta`**。绝大多数订阅服务按 UA 决定返回格式：用 curl 默认 UA（`curl/8.x`）去拿，拿回来是 **base64 编码的 v2ray 链接**而不是 YAML —— 现象是「订阅下载成功、proxies 0 行、节点一个都没有」，极易被误判成解析 bug。三处下载（`install_callback` / `config_callback` / `refresh_sub`）统一走 `cmd/sub_download.sh` 的 `download_subscription()`：带 `clash.meta` 取一次，不像 Clash YAML 就换 `ClashforWindows/0.20.39` 再取一次，仍不像就把原文件留下供诊断（返回码 2）。**新增下载入口时不要再直接写 `curl`**，否则这个坑会再踩一遍。
34. **订阅解析出 0 个节点必须判失败**。`proxies:` 与 `proxy-providers:` 都为 0 时，config.yaml 里一条节点都没有，用户却看到「已启用」。现在 `config_callback` 会：①用 `diagnose_subscription()` 判定是 base64 / HTML / JSON / 缺段；②**旧配置里还有节点就保留旧配置并 `exit 4`**（绝不用空配置洗掉还能用的节点）；③旧配置也没节点才继续写，并仍以 `exit 4` 收尾。管理服务按返回码分流：**3 = 内核重启失败、4 = 订阅无节点**，其余按未知错误处理。
35. **`exit 4` 的诊断要从日志里捞，别在服务端另写一套判断**：`admin_server._last_interesting_log_line()` 按关键词优先级从 `info.log` 尾部取最后一条命中行（优先取「为什么坏」的诊断行，再取「做了什么」的处置行），把脚本的原话带回接口错误信息。两处判断各写一遍必然对不上。
36. **YAML 允许「序列与父键同缩进」，这是本项目最贵的一个坑**。机场订阅（subconverter 之类转换出来的）大量使用「顶格 `-`」写法：

    ```yaml
    proxies:
    - name: "香港 01"
      type: ss
    ```

    这份 YAML 对内核是**合法**的，节点能正常加载；但任何「按缩进切段」的简易解析器（管理服务的 `parse_config_proxies`、脚本里的 `config_has_nodes`）都会在**第一个顶格 `-` 处认为 `proxies` 段已经结束**，于是读出 0 个节点。现象极具迷惑性：订阅下载成功、config.yaml 重新生成成功、内核也正常跑着，**唯独管理界面一个节点都没有**，用户完全看不出问题在哪（v2.8.5 之前就是这样）。现在有三道防线：①`extract_yaml_block` 抽完块统一 `normalize()`，把顶格列表项缩进到父键下，输出规范 YAML；②`admin_server.parse_config_proxies` 兼容顶格写法（**升级后不用重新应用配置也能立刻看到节点**，因为 NAS 上已有的旧 `config.yaml` 还是顶格的）；③生成 `config.yaml` 时先写临时文件、`count_yaml_nodes()` 回读校验节点数，0 节点直接 `exit 4` 且**不覆盖正在生效的配置**。**新增任何按 YAML 缩进切段的解析代码，都必须同时认这两种写法**（判定段结束的条件是「顶格且不是 `-`」）。另：脚本里数节点条数统一用 `count_yaml_nodes()`，不要另写一份计数逻辑——两份计数必然对不上。**「生成结果 0 节点就判失败」这条只针对「订阅里本来就有内联节点」**：`proxy-providers` 型订阅的节点由 provider 在运行时拉取，`config.yaml` 里本来就没有内联节点，不能当成失败（判定用 `SUB_INLINE_NODES`，不是 `SUB_HAS_NODES`）。

37. **切换订阅不再「全家桶重启」**：`config_callback` 过去用 `main stop` + `main start` 应用新配置，会顺手停掉刷新守护进程和管理服务，且内核重启到完成之间有十几秒窗口，fnOS 靠 `main status`（0=运行 / 3=停止）巡检，抓到这一瞬就把应用标成「已停止」——这正是「切换订阅后应用直接停止、需要手动启动」的成因。现在改为 **`main restart-kernel`**：①**只重启内核进程**，管理服务与订阅刷新守护进程原地不动；②重启窗口内 `main` 写 `RESTART_FLAG`（数据目录 `restarting`，内容为持标进程 PID），`status` 在标记有效期内（持标进程存活且未超 5 分钟）仍返回 0，fnOS 不会误判；③内核启动统一 `nohup`，避免被 `config_callback` / `restart-kernel` 这些瞬态调用进程退出时的 SIGHUP 带走。只有在需要整族重启（改到 TUN 之外的运行参数）时才用 `main restart`。`restart-kernel` 与 `restart` 都加 `mark_restarting`/`clear_restarting` 成对包裹。
38. **切换 / 保存配置要有进度反馈 + 屏蔽并发**：`config_callback` 全程写 `apply.progress`（`stage` / `percent` / `message`，UTF-8），阶段为 `downloading` → `parsing` → `generating` → `restarting` → `done` / `failed`；管理服务把「应用配置」改成**后台线程异步执行**，POST `/api/settings`、`/api/subscriptions` 返回 **202**（而非同步 200），前端轮询 `/api/apply/progress`，并在应用期间弹**遮罩**阻止用户再点切换 / 保存。「切换订阅」与「定时刷新」通过 `apply.lock`（目录锁 + `pid` 文件，跨进程串行化）互斥，冲突时 `config_callback` 返回 **5**，前端提示「另一个操作正在进行」。新增「触发配置应用」的路径时，务必：①带 `MIPROXY_KEEP_ADMIN=1`（不杀管理服务）；②不要自己 `main stop`（用 `restart-kernel`）；③写 `apply.progress` 让前端有反馈；④尊重 `apply.lock`（守护进程自己持锁调用时传 `MIPROXY_APPLY_LOCKED=1`，避免自锁死）。
39. **重装 / 升级不再抹掉面板已配的订阅**：fnOS 升级时再次执行 `install_callback`，而它原本无条件整体重写 `settings.conf`，于是「首次装选了『暂不配置』→ 之后在面板配好远程订阅 → 更新安装又被打回 none」。现在 `install_callback` 检测到既有 `settings.conf` 时，向导给的是默认值（none / 空）就**沿用旧配置**（订阅类型 / 链接 / 密钥 / HTTP/API 端口 / 刷新间隔 / 管理端口令牌全保留），只有向导显式给了新值才覆盖；首次安装行为不变。判定「显式指定」的规则：远程订阅需 `wizard_sub_type=remote` 且 `wizard_sub_url` 非空、本地订阅需路径真实存在。在管理界面改过订阅后重装不会丢，除非你确实想在向导里重新指定。

---

## 10. 安全红线

- 凡涉及外部输入（订阅 URL、用户名、密钥、本地路径）的拼接，沿用现有转义/白名单做法（如 §5.4 的域名提取），**禁止**直接把 `wizard_*` 变量拼进 `eval`/命令字符串，防命令注入。
- `secret`、`wizard_proxy_pass` 等敏感字段只写进 `settings.conf`（仅本机 root 可读），不要回显到日志或前端（管理接口只返回 `*_set` 布尔值）。
- 管理服务（9091）监听 `0.0.0.0`：无密钥时**只读放行、写操作全拒**（`_write_blocked()`）。新增接口必须走 `_authorized()`，静态资源必须保留 `_serve_static` 的路径穿越校验，日志读取必须限制在 `LOG_WHITELIST`。
- 不要提交真实订阅 URL、密钥、凭据到仓库。
