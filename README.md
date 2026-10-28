# MiProxy

<div align="center">

![Logo](https://img.shields.io/badge/MiProxy-mihomo-blue?style=for-the-badge)
[![GitHub Release](https://img.shields.io/github/v/release/56025192/miproxy-fpk?style=for-the-badge)](https://github.com/56025192/miproxy-fpk/releases)
[![Download](https://img.shields.io/github/downloads/56025192/miproxy-fpk/total?style=for-the-badge)](https://github.com/56025192/miproxy-fpk/releases)
[![License](https://img.shields.io/github/license/56025192/miproxy-fpk?style=for-the-badge)](#许可证)

**把飞牛 NAS 变成你的私人代理网关**

基于 [mihomo](https://github.com/MetaCubeX/mihomo) 内核，一键开启 TUN 全局代理，订阅自动更新，流量完全私有

</div>

---

> 还在为每台设备单独配置代理烦恼？一台永不离线的 NAS，就是你最稳定的代理节点。

---

## 为什么是 MiProxy

| 痛点 | MiProxy 的答案 |
|------|---------------|
| 手机、电脑、TV 每台都要配代理 | **TUN 模式**接管全局，所有设备零配置联网 |
| 订阅节点经常失效要手动更新 | **自动定时刷新**，睡一觉醒来自动同步最新节点 |
| 云服务贵、第三方节点不稳 | **自建代理网关**，NAS 24 小时开着，节点自己选 |
| 流量经过第三方，数据不放心 | **流量完全私有**，经过你自己的磁盘再出去 |

---

## 功能特性

- **TUN 全局代理** — 开启后整个局域网设备流量自动走代理，无需逐台配置
- **订阅自动更新** — 支持远程订阅 URL / 本地配置文件，可设 1-24 小时自动刷新
- **内核在线升级** — 管理界面里检查 mihomo 官方新版本，一键升级或切到指定版本；升级前自动备份、起不来自动回滚
- **双控制面板** — [Zashboard](https://github.com/Zephyruso/zashboard)（极简现代）/ [MetaCubeXD](https://github.com/MetaCubexD/MetaCubeXD)（功能全面），安装时任选
- **全协议支持** — VMess / VLESS / Trojan / Shadowsocks / Hysteria2 等主流代理协议
- **智能路由** — 内置 GeoIP / GeoSite / MMDB 数据库，国内外流量自动分流
- **DNS 防泄露** — 启用 strict-route，DNS 请求完全由代理处理，无泄露风险
- **TUN 参数可调** — 可在安装向导中选择开启/关闭 TUN 模式

---

## 系统要求

| 项目 | 要求 |
|------|------|
| fnOS 版本 | ≥ 1.1.0 |
| 架构 | x86_64 / arm64 |
| 安装包大小 | ~35 MB |
| 运行内存 | 建议 ≥ 512MB |
| 系统依赖 | **python3**（解析订阅必需） |

> 💡 fnOS 通常已内置 `python3`。若系统被精简掉它，订阅虽然能下载但**解析不出节点**，`info.log` 会记录「未找到 python3」。详见 [日志与排障](#日志与排障)。

---

## 快速开始

### 1. 安装

从 [Releases](https://github.com/56025192/miproxy-fpk/releases/latest) 下载对应架构的 `.fpk` 文件，登录 fnOS → **应用中心** → **安装本地应用** → 上传文件，按向导完成安装。

### 2. 配置订阅

安装完成后，在 fnOS 应用中心打开 MiProxy 的 **「配置」** 页，在「订阅管理」步骤填入订阅链接或指定本地文件路径并保存；节点可在控制面板（`http://NAS_IP:9090`）中查看。

### 3. 开启 TUN 模式（可选）

在向导中选择开启 TUN 模式，NAS 将接管局域网所有设备的代理流量，无需在每台设备上配置代理地址。

---

## 代理端口说明

| 端口 | 协议 | 用途 |
|------|------|------|
| 7890 | HTTP | HTTP/HTTPS（Mixed）代理，手动代理时使用 |
| 9090 | HTTP | 控制面板 / API |
| 9091 | HTTP | 在线管理界面（订阅 / 端口 / 日志），本版本新增 |
| 53 | DNS | 内核 DNS（TUN 下由 `dns-hijack` 接管） |

---

## 使用指南

### 1. 打开控制面板

安装后在 fnOS 应用列表点击「MiProxy 控制面板」，会打开**面板选择页**：

- 选择页：`http://NAS_IP:9090/ui/index.html`
- **Zashboard**（默认，现代简洁）：`http://NAS_IP:9090/ui/frame-zashboard.html`
- **MetaCubeXD**（经典，功能全面）：`http://NAS_IP:9090/ui/frame-metacubexd.html`

> 面板打不开时先确认服务已启动（`info.log` 出现「miproxy 启动完成」），并检查 9090 端口是否被占用。

三个界面之间可以互相往返：面板选择页点卡片进入各面板；**每个面板页面的顶部都有一行导航条**，左侧「‹ 返回面板选择」一键回到选择页，右侧标注当前面板名称。

> 🔧 Zashboard / MetaCubeXD 是独立的前端产物，MiProxy **不修改它们的文件**。两个面板各自跑在一个带顶栏的包装页里（`frame-zashboard.html` / `frame-metacubexd.html`，用 iframe 承载），顶栏与面板内容各占各的版面，因此**不会遮挡面板的任何操作元素**，面板也能按自己的视口高度正常渲染。

### 2. 配置订阅

- 入口：fnOS 应用中心 → MiProxy → **「配置」** → 「订阅管理」；或管理界面（9091）的「订阅管理」卡片。
- **远程订阅**：填入 Clash 订阅 URL（仅支持 **Clash / mihomo 格式**，即包含 `proxies:` 或 `proxy-providers:` 的 YAML）。
- **本地订阅**：指定 NAS 上的 YAML 文件路径（示例默认 `/vol1/@appshare/miproxy/sub.yaml`）。
- 保存后会自动重新生成 `config.yaml` 并重启内核。
- 安装时选「暂不配置」也没关系，之后在配置页补填即可生效（前提：系统有 `python3`）。

> 💡 **可以保存多个订阅**（远程 / 本地混合），但**同一时间只有一个处于「启用」状态**，只有启用项会被写进 `config.yaml`。切换启用项在管理界面（9091）里点选左侧单选即可，切换后会立即重新生成配置并重启内核。

> ⚠️ **「订阅下载成功，但一个节点都没有」多半是格式问题，不是下载问题。** 绝大多数订阅服务会根据请求的 `User-Agent` 决定返回什么格式：用普通客户端（curl）去拿，拿回来常常是 **base64 编码的 v2ray 链接**，而不是 YAML。MiProxy 抓取订阅时会带 `clash.meta` 的 UA，并在拿到的内容不像 Clash YAML 时自动换一个 UA 重试一次。仍然拿不到节点时，界面会**直接告诉你原因**（base64 / 返回的是网页 / 缺少 `proxies:` 段），并且**保留原来那份还能用的配置**，绝不用空配置把现有节点洗掉。

### 3. 自动刷新

在订阅设置中选择刷新间隔（1 / 6 / 12 / 24 小时，或关闭）。启用后由后台守护进程 `refresh_sub` 定时执行三步：

1. 拉取**当前启用的远程订阅**到缓存 `proxy_providers/subscription.yaml`；
2. 用新缓存**重新生成 `config.yaml`**（节点是内嵌进配置的，只换缓存不重生成 = 没刷新）；
3. 重启内核，让新配置真正加载。

全过程写入 `refresh_sub.log`，成功时会有一行「已应用新订阅：config.yaml 已重生成，mihomo 已重启」。任一步失败会保留上一份可用配置，不会把内核停在半路。管理界面的「立即刷新启用项」走的是同一套逻辑（`refresh_sub once`）。

> ⚠️ 因此定时刷新期间内核会重启一次（秒级），正在传输的连接会短暂中断，属预期行为；对稳定性要求高的场景请把间隔调大或关闭自动刷新。

### 4. 接入设备

| 方式 | 配置 |
|------|------|
| 手动代理 | 设备代理地址填 `NAS_IP:7890`（HTTP/HTTPS Mixed） |
| TUN 全局 | 开启后局域网设备流量自动接管，无需逐台配置（需 root，网卡名 `miproxy`） |
| 代理认证 | 若向导设置了用户名/密码，客户端需一并填写 |

### 5. 在线管理界面（9091）

除 Zashboard / MetaCubeXD 之外，MiProxy 自带一个**轻量管理页**，用于不开面板也能改配置：

- 入口：面板选择页第三张卡片「MiProxy 管理」，或直接访问 `http://NAS_IP:9091/admin/`
- **顶部菜单分页**：概览 / 订阅 / 代理与面板 / 节点 / 内核 / 日志，地址栏形如 `#/subscriptions`，可直接收藏某个页面、也能用浏览器前进后退
- 能力：

| 模块 | 说明 |
|------|------|
| 概览 | 内核运行状态 / PID、管理端口、`config.yaml` 与订阅缓存的大小与时间、当前启用订阅、自动刷新间隔、内核版本、最近 20 行日志与快捷入口 |
| 订阅管理 | **多订阅清单**：保存多个远程 / 本地订阅，单选切换启用项（同时只有一个生效），支持新增 / 编辑 / 删除 / 停用 |
| 一键刷新 | 「立即刷新启用项」= 重新下载（远程）/ 同步（本地）→ 重新生成 `config.yaml` → 重启内核 |
| 代理与面板 | 代理端口、面板端口、代理用户名/密码、面板密钥、TUN 开关 |
| 节点 | 列出 `config.yaml` 里**实际生效**的节点（名称 / 类型 / 地址 / 延迟），支持搜索、只看可用、单个或全部测速 |
| 内核 | 检查 mihomo 官方最新版本、一键升级、按版本切换、恢复安装包内置内核、备份回滚、下载镜像前缀 |
| 日志 | 在线查看 `info.log` / `refresh_sub.log` / `install_callback.log` / `config_callback.log` / `upgrade_callback.log` / `admin.log`，支持 3 秒自动刷新（仅在该页生效） |

节点页的行为约定：

- 节点清单**直接解析 `config.yaml`**，而不是读订阅缓存——缓存有节点不代表内核真的加载了；两份都看只会让人更困惑。
- 首次进入显示的延迟取自内核的 `history`（最近一次测过的结果，可能很旧）；点「测速」才是**现测**。
- 测速由后端并发调用内核的 `/proxies/{name}/delay`（并发 6、单个 4 秒超时、单批上限 300 个），测试地址默认 `http://www.gstatic.com/generate_204`，可在请求里改。
- 内核未运行时不做假测速，直接返回「内核未运行，无法测速」；页面上也会出现对应提示 chip。
- 测速是只读操作（不写配置、不动进程），因此**即使没设面板密钥也允许调用**——刚装好还没填密钥时也能先确认节点通不通。其余写操作在未设密钥时一律 403。

内核升级的行为约定：

- 「检查更新」拉取 `MetaCubeX/mihomo` 的 GitHub Releases，并按本机架构（amd64 / arm64 / armv7 …）自动挑选对应的资产；
- 切换流程固定为：**下载 → 校验（跑 `<bin> -v` 识别版本）→ 备份当前内核 → 原子替换 → 重启内核**，全程在后台线程执行，页面轮询显示进度；
- 新内核起不来（`main restart` 失败或 `main status` 不为运行）会**自动回滚**到刚备份的版本并再次重启，因此不会把设备留在「没有内核」的状态；
- 备份保留最近 3 个（`kernel_backup/mihomo-<版本>-<时间>`），可随时一键回滚；
- 升级应用包（`.fpk`）**不会覆盖**你手动切换过的内核：内置内核另存为 `mihomo.bundled`，需要时点「恢复内置内核」；
- 国内访问 GitHub 慢时，在「下载源」填镜像前缀（如 `https://ghfast.top/`，拼成 `镜像 + 原始地址`）；也可直接把内核文件放到 NAS 上，用「从文件安装」；
- 下载地址只从官方 Releases 或你填的镜像推导，不接受任意 URL——避免把管理接口变成「下载任意可执行文件」的通道。

行为约定：

- 保存后复用与「配置」向导**完全相同**的生成逻辑（`cmd/config_callback`），因此端口修改、TUN 优化项、订阅字段同步完全一致。
- 订阅清单存在 `settings.conf` 同目录的 `subscriptions.json`；**启用项**仍落盘为 `wizard_sub_type` / `wizard_sub_url` / `wizard_sub_path`，所以内核、刷新守护进程、安装回调都只认这一个订阅，不存在「多个同时生效」的可能。
- 仅**切换启用项**会重启内核；单纯新增 / 改名 / 删除不动内核。
- 自动刷新间隔在「订阅」页，修改后点该页的「保存刷新间隔」生效（等同一次配置应用，会重启内核与服务）。
- 正在启用的订阅不能直接删除（先切到其它订阅或选「不使用订阅」），避免误操作把节点清空。
- 若在向导里改过订阅 URL（不在清单中），管理页会显示一条「当前配置（来自安装/配置向导）」，编辑保存后即成为正式条目。
- 密码 / 密钥字段**留空表示保持不变**，勾选「清除」才会清空；避免误抹导致面板打不开。
- 若在向导里设置过**面板密钥**（`wizard_secret`），管理接口需要同样的密钥，页面顶部会出现输入框；未设置则局域网内可直接访问。
- 管理页访问内核控制端口（读节点、测速、取版本）时用的密钥，取的是 **`config.yaml` 里的 `secret`**——那是内核真正在用的那份。若你改了面板密钥但没重新应用配置，两者会不一致，此时按设置项去请求只会拿到 401、节点页整页显示 `unauthorized`；以内核实际配置为准可以避开这个坑。
- 鉴权令牌保存在浏览器 `localStorage`，换浏览器/清缓存需重新输入。
- 管理服务由 `cmd/main` 在应用启动时拉起（`admin.pid` / `admin.log`），与内核相互独立：内核挂掉时管理页依然可用，方便排查。
- **运行期脚本会同步一份到数据目录**（`/vol1/@appdata/miproxy/cmd/`），管理服务优先用这份。原因见下面这条：安装/升级时 fnOS 先在 `/vol1/appcenter-downloads/<包名>` 解包并从中执行回调，装完该目录被清理；若管理服务仍从那里取脚本，就会「页面正常、一保存就报找不到 config_callback」。同步副本与安装目录每次启动比对内容，升级后不会用到旧版本。

> 🔐 管理接口未设密钥时对局域网开放，请勿将 9091 直接暴露到公网；建议在向导设置面板密钥，或仅在可信内网使用。

---

## 目录与文件说明

| 路径 | 说明 |
|------|------|
| `/var/apps/miproxy/` | 应用安装目录（`app/`、`cmd/`、`wizard/`、`manifest`） |
| `/vol1/@appdata/miproxy/` | **数据目录**：`config.yaml`、日志、PID、订阅缓存、Geo 数据、`target/mihomo`、`ui/` |
| `/vol1/@appconfig/miproxy/settings.conf` | 向导保存的配置（`wizard_*` 键值），其中 `wizard_sub_*` 即**当前启用**的订阅 |
| `/vol1/@appconfig/miproxy/subscriptions.json` | 订阅清单（可多个），`active_id` 指向启用项 |
| `/vol1/@appconfig/miproxy/kernel.json` | 内核管理的本地状态：下载镜像前缀、缓存的版本列表与上次检查时间 |

数据目录典型结构：

```
/vol1/@appdata/miproxy/
├── config.yaml                 # mihomo 主配置（脚本自动生成，勿手改）
├── info.log                    # 内核与启停日志
├── refresh_sub.log             # 订阅刷新日志
├── admin.log                   # 管理服务（9091）访问与异常日志
├── install_callback.log        # 安装回调日志
├── config_callback.log         # 配置变更回调日志
├── upgrade_init.log / upgrade_callback.log   # 升级日志
├── app.pid / refresh_sub.pid / refresh_sub.lock / admin.pid
├── proxy_providers/subscription.yaml          # 当前生效的订阅缓存
├── target/mihomo               # 当前内核二进制（安装时按架构拷贝，可在管理界面切换/升级）
├── target/mihomo.bundled       # 安装包自带的内核副本（用于「恢复内置内核」）
├── kernel_backup/              # 内核备份（mihomo-<版本>-<时间>，保留最近 3 个）
├── ui/                         # 控制面板静态资源（含 ui/admin 管理页）
└── geoip.dat / geosite.dat / Country.mmdb      # 分流数据库
```

> ⚠️ `config.yaml` 由 `cmd/install_callback` 与 `cmd/config_callback` 自动生成，**手动修改会在下次保存配置时被覆盖**。需要持久化自定义项，请改订阅源或脚本。

---

## 项目结构

```
miproxy-fpk/
├── manifest                     # 包元数据（应用名/版本/端口/权限）
├── app/
│   ├── server/{x86_64,arm64}/mihomo   # 内核二进制（按架构）
│   ├── data/                    # GeoIP.dat / GeoSite.dat / Country.mmdb
│   └── ui/                      # 面板：zashboard/、metacubexd/、admin/（管理页）、index.html（选择页）、config、images/
├── cmd/                         # fnOS 生命周期回调（bash）
│   ├── main                     # 启动 / 停止 / 重启 / 状态（含刷新守护进程、管理服务）
│   ├── refresh_sub              # 订阅刷新守护进程（`once` 为单次「刷新并应用」）
│   ├── admin_server.py          # 管理界面后端（Python3 标准库，端口 9091）
│   ├── install_init  / install_callback    # 安装前 / 安装后
│   ├── config_init   / config_callback     # 配置变更前 / 后
│   ├── upgrade_init  / upgrade_callback    # 升级前 / 后
│   └── uninstall_init / uninstall_callback # 卸载前 / 后
├── scripts/
│   └── dev-local.sh             # 本地开发辅助（造开发目录 / 起管理服务 / 同步前端），不进安装包
├── config/
│   ├── privilege                # 权限声明（run-as: root，TUN 必需）
│   └── resource                 # 数据共享声明
└── wizard/
    ├── install                  # 安装向导界面定义
    ├── config                   # 配置向导界面定义
    └── uninstall                # 卸载向导界面定义
```

---

## 常见问题（FAQ）

### Q1：安装时没填订阅，之后在配置页补填却“没生效”？

按顺序排查：
1. 系统是否有 `python3`（订阅解析依赖它）——看 `/vol1/@appdata/miproxy/info.log` 是否出现「未找到 python3」；
2. 是否有 `[extract_yaml_block] 读取订阅缓存失败`——订阅文件不可读或格式不符；
3. 字段统计是否全为 `0行`——订阅没下载成功，或不是 Clash/mihomo 格式。

> 历史版本的 `config_callback` 存在订阅解析脚本语法错误，会导致“改了不生效”；当前版本已修复，并把失败原因写入 `info.log`。

### Q2：控制面板打不开 / 空白？

确认服务已启动（`info.log` 有「miproxy 启动完成」）、9090 未被占用；入口是 `http://NAS_IP:9090/ui/index.html`。

### Q3：开了 TUN，设备却没走代理？

TUN 需 root（已声明），网卡名固定 `miproxy`；检查 `config.yaml` 的 `tun.enable`、`strict-route`，并确认系统已开启 IP 转发。部分网络环境不支持 TUN，可先用手动代理（7890）验证节点本身可用。

### Q4：订阅更新了但节点没变？

先在管理界面（9091）确认**当前启用的是哪一条订阅**（多订阅时很容易改错条目），再查看 `refresh_sub.log`；远程订阅需 `wizard_sub_type=remote` 且刷新间隔 > 0。也可点「立即刷新启用项」触发一次拉取。

判断「到底有没有生效」只看两处：`config.yaml` 里是不是新节点、`info.log` / `refresh_sub.log` 有没有重启记录。节点是**内嵌**在 `config.yaml` 里的，所以只替换订阅缓存而不重生成配置、不重启内核，等于没生效——这一步 `refresh_sub` 已经补齐。

### Q5：支持什么订阅格式？

仅 **Clash / mihomo YAML**（含 `proxies:` 或 `proxy-providers:`）。Base64 的 v2ray 订阅需先转换为 Clash 格式。

### Q6：端口冲突怎么办？

默认 7890（代理）、9090（面板）。如需调整 `wizard_http_port` / `wizard_api_port`，请确保不与 NAS 上其它服务冲突，否则内核可能启动失败。

### Q7：日志在哪？

数据目录 `/vol1/@appdata/miproxy/` 下的 `info.log`、`refresh_sub.log`、`admin.log`、`install_callback.log`、`config_callback.log` 等。也可以直接在**管理界面（9091）**的「日志」卡片里切换查看。

### Q8：管理界面（9091）打不开 / 提示未授权？

- 打不开：确认 `cmd/main` 已拉起管理服务（`ps` 里应有 `admin_server.py`，或看 `admin.log`）；若 NAS 上 `python3` 缺失，管理服务会跳过启动并在 `info.log` 记「未找到 python3」。
- 端口被占：可用 `wizard_admin_port` 指定其它端口（写入 `settings.conf` 后重启应用）。
- 提示未授权（401）：向导里设过**面板密钥**，在页面顶部输入同一个密钥；忘记则改回 `settings.conf` 里的 `wizard_secret`，或通过「配置」向导重设。
- **没设过密钥却不能保存（403，提示"未设置面板密钥"）**：这是安全策略——管理服务监听 `0.0.0.0`，没有密钥时等于局域网内任何人都能改配置、甚至通过「内核」页装一个任意二进制并以 root 运行，所以**未设密钥时只读、禁止一切写操作**。到 fnOS 应用中心 → MiProxy → 设置 →「面板密钥」填一个值并保存，再回管理页点顶部的「连接」即可。
- 页面空白：浏览器强制刷新（Ctrl+F5）清缓存，管理页为单文件 HTML，无外部依赖。

### Q9：能同时启用多个订阅吗？

不能，也不建议——MiProxy 的设计是**清单可多个、生效只有一个**：订阅清单保存在 `subscriptions.json`，但生效项会写进 `settings.conf` 的 `wizard_sub_*`，内核与刷新守护进程只读这两处。需要换订阅时在管理界面点选另一条的单选即可，旧的订阅配置会原样保留，随时切回。

想让多个订阅「合并」节点，请在订阅源侧合并（或用 `proxy-providers` 汇总），再保存成一条订阅。

### Q10：怎么升级 mihomo 内核？升坏了怎么办？

管理界面「内核」页 → 「检查更新」→ 有新版时点「升级到 vX.Y.Z」（或在下方选/填版本号后「切换到此版本」）。升级期间代理会中断几秒。

- **升坏了**：新内核启动失败会自动回滚到升级前的版本；也可以手动在「备份与回滚」里选任一备份点「回滚」。
- **想回安装包自带版本**：点「恢复内置内核」（来源是 `target/mihomo.bundled`）。
- **检查更新失败**（NAS 访问不了 GitHub）：在「下载源」填镜像前缀（如 `https://ghfast.top/`），或把内核文件放到 NAS 上后「从文件安装」，也可以直接填版本号安装。
- **升级后仍需重启应用吗**：不需要，切换成功时已经重启过内核；`settings.conf` 与订阅配置都不受影响。

### Q11：升级了 .fpk 应用包，内核会被换回去吗？

不会。安装/升级回调只在 `target/mihomo` **不存在**时用内置内核补齐；已存在时只把内置版本另存为 `target/mihomo.bundled`，并在 `info.log` 记录「保留当前版本」。想换回内置内核请用「恢复内置内核」，避免"手动升级的内核被悄悄覆盖"。

### Q12：节点页是空的 / 全部显示「超时」/ 显示 `unauthorized`？

按这三条依次看：

1. **一个节点都没有** → `config.yaml` 里确实没有 `proxies`。最常见的原因是**订阅返回的不是 Clash YAML**（常见是 base64 编码的 v2ray 链接）——这种情况下「订阅」页保存时会直接告诉你原因，例如「订阅返回的是 base64 编码内容，需先转换成 Clash/mihomo 格式」，并且原配置会被保留。其次是订阅只保存了没点应用，配置不会重生成。
2. **有节点但全部「超时」** → 先看「概览」页内核是否在运行（未运行会直接提示无法测速）；内核正常则多为节点本身失联，或 NAS 出网被拦（测速走的是内核自己的代理链路，不是管理服务的网络）。
3. **全部显示 `unauthorized`** → 管理页访问内核控制端口的密钥与 `config.yaml` 里的 `secret` 不一致。当前版本已改为直接读 `config.yaml` 的 `secret`，通常不会再出现；若仍是，检查 `config.yaml` 是否被手工改过 `secret` 而后台服务又在读另一份。

### Q13：保存配置时提示「找不到 config_callback 脚本：/vol1/appcenter-downloads/miproxy-xxx/cmd/config_callback」？

这是 **v2.8.1 及更早版本**的已知问题：fnOS 安装/升级时会先在 `/vol1/appcenter-downloads/<包名>` 解包、并从那里执行安装回调，回调再拉起管理服务；装完这个临时目录被系统清理，但进程还在（代码已在内存、页面从数据目录读），于是它按脚本自身位置去找 `config_callback` 就找不到了。表现就是「页面一切正常，一保存就报错」。

**临时解决**：到 fnOS 应用中心把 MiProxy 重启一次（或升级到 v2.8.2 及以后）。**v2.8.2 起**已从两个方向修死：运行期脚本会同步一份到 `/vol1/@appdata/miproxy/cmd/` 并优先使用；脚本查找也会依次尝试安装目录、数据目录、`/var/apps`、`/var/packages`，不再只看脚本自己所在的目录。若仍报此错，报错信息里会把查过的目录全部列出，按图索骥即可。

### Q14：保存配置后管理页面直接崩掉 / 要手动重启应用？

**v2.8.4 已修复**。原因是改配置时 `config_callback` 会调用 `main stop` 重启内核，而 `main stop` 默认把**管理服务也一起停掉**——用户正开着的页面当场断线，看起来就像应用崩了；而 fnOS 向导触发的那条路径根本不会带上「保留管理服务」的标记，所以必停。

现在 `config_callback` 自己无条件保留管理服务，内核重启与管理服务彻底解耦：保存配置时页面不会断，重启内核期间页面照常能用。若升级后仍遇到，刷新一下页面即可。

---

## 日志与排障

| 日志 | 内容 |
|------|------|
| `info.log` | 内核启动/停止、config 生成、订阅字段解析统计 |
| `refresh_sub.log` | 定时订阅刷新（下载 / 校验 / 重启） |
| `admin.log` | 管理服务（9091）请求与异常 |
| `install_callback.log` / `config_callback.log` | 安装与配置变更回调 |

常见错误对照：

- **未找到 python3** → 安装 `python3` 后重新保存配置。
- **mihomo 二进制不存在或不可执行** → 架构不匹配或安装不完整（重装对应架构的 `.fpk`）。
- **`[extract_yaml_block] 读取订阅缓存失败`** → 订阅缓存不可读（检查路径与权限）。
- **订阅下载失败** → NAS 无法访问订阅地址（网络 / DNS / URL 问题）。
- **字段全 `0行`** → 订阅非 Clash 格式或内容为空。

订阅是否下载成功，可直接查看 `proxy_providers/subscription.yaml`。

---

## 升级与卸载

- **升级**：在 fnOS 应用中心上传新版 `.fpk`，升级回调会保留数据目录与配置。
- **卸载**：通过应用中心卸载。如需彻底清理，可手动删除 `/vol1/@appdata/miproxy/` 与 `/vol1/@appconfig/miproxy/`（将丢失配置与订阅缓存）。

---

## 本地开发

本项目**没有编译步骤**：代码主体是 `cmd/` 下的 bash 生命周期脚本 + `cmd/admin_server.py`（Python3 标准库）+ `app/ui/` 静态前端，改完即生效，不需要 `npm build` / `go build`。

**最快路径**（不用打包，两条命令起来）：

```bash
bash scripts/dev-local.sh init      # 造开发目录 + 跑 install_callback，生成 config.yaml / settings.conf
bash scripts/dev-local.sh admin     # 前台起管理界面：http://127.0.0.1:9091/admin/
```

其余子命令：`status`（状态面板）、`sync-ui`（改完前端同步）、`restart`（改配置后重启内核）、`stop`、`clean`。脚本读 `MIPROXY_DEV_ROOT`（默认 `/tmp/miproxy-dev`）、`MIPROXY_ARCH`、`ADMIN_PORT`，向导字段直接透传：

```bash
wizard_sub_type=remote wizard_sub_url="https://example.com/clash.yaml" bash scripts/dev-local.sh init
```

> **能跑到什么程度**：bash 回调、配置生成与校验、管理界面（含订阅管理、日志、内核页）全都能本地跑通；**只有 mihomo 内核本身跑不起来**——它是 Linux ELF 二进制，Windows 上无法执行（`/api/status` 会显示 `running: false`，内核版本读不到，属预期）。要验证真实代理 / TUN，得在 WSL、Linux 或 NAS 上跑。

下面的内容是手动版细节与原理，脚本只是把它们串起来。

### 1. 环境准备

| 需要 | 用途 | 说明 |
|------|------|------|
| `bash` | 跑 `cmd/` 脚本 | Windows 用 Git Bash；注意别让 WSL 的 `bash` 抢先（见下方坑 1） |
| `python3` ≥ 3.8 + `pyyaml` | 生成/校验 `config.yaml`、管理服务 | `pyyaml` 仅调试校验用，运行时配置由 mihomo 解析 |
| `git` | 版本管理 | — |
| [fnpack](https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64) | 本地打包 `.fpk` | 官方只发 `linux-amd64` 单文件，Windows 请在 WSL / Docker 里跑 |

### 2. 造一个"假装机目录"

fnOS 跑回调时会注入三个路径变量，本地手动 `export` 成临时目录即可完全模拟：

```bash
APP=/tmp/mp/app    # ← TRIM_APPDEST：解包后的安装目录（装的是仓库里的 app/）
VAR=/tmp/mp/var    # ← TRIM_PKGVAR：数据目录（config.yaml、target/mihomo、日志、ui/）
ETC=/tmp/mp/etc    # ← TRIM_PKGETC：配置目录（settings.conf、kernel.json）

rm -rf /tmp/mp && mkdir -p "$APP" "$VAR" "$ETC"
cp -rf app/. "$APP"/

# 仓库里两个架构的内核并存，装机只能有一个：按当前机器选一个放到位
ARCH=x86_64        # ARM 机器改成 arm64
cp -f "$APP/server/$ARCH/mihomo" "$APP/server/mihomo"
rm -rf "$APP/server/x86_64" "$APP/server/arm64"

export TRIM_APPDEST="$APP" TRIM_PKGVAR="$VAR" TRIM_PKGETC="$ETC"
export TRIM_TEMP_LOGFILE=/tmp/mp/tmp.log
```

### 3. 跑生命周期回调

向导字段全部以 `wizard_*` 环境变量传入（字段名见 `wizard/install`）：

```bash
wizard_sub_type=remote \
wizard_sub_url="https://example.com/clash.yaml" \
wizard_refresh_interval=6 \
wizard_http_port=7890 wizard_api_port=9090 \
wizard_secret=test123 \
wizard_tun_enable=false \
bash cmd/install_callback

cat "$VAR/install_callback.log"     # 装完看日志
cat "$ETC/settings.conf"            # 落盘的配置项
cat "$VAR/config.yaml"              # 生成的 mihomo 配置
```

> `install_callback` 末尾会自动 `main start`（拉起内核 + 管理服务）。本地没有 root / TUN 时内核起不来是正常的，只关心日志和生成的文件即可；这一步若把你手动起的管理服务杀掉，加 `MIPROXY_KEEP_ADMIN=1`。Windows 上 NTFS 不保留可执行位，日志里可能出现 `mihomo 二进制不存在或不可执行`——反正 Linux 内核二进制本来也跑不了，忽略即可。

配置变更走同一套约定：

```bash
wizard_sub_type=remote wizard_sub_url="https://example.com/new.yaml" \
bash cmd/config_callback

bash cmd/main status      # 查看运行状态
bash cmd/main restart     # 重启内核（改配置后必须重启才生效）
bash cmd/main stop
```

> `config_callback` 在不带任何 `wizard_*` 时会**回退复用** `settings.conf` 里的现值，只改你想改的那一项即可。

### 4. 单独跑管理服务（9091）

```bash
TRIM_APPDEST="$APP" TRIM_PKGVAR="$VAR" TRIM_PKGETC="$ETC" \
ADMIN_PORT=9091 python3 cmd/admin_server.py
# 打开 http://127.0.0.1:9091/admin/
```

- 端口优先级：`ADMIN_PORT` 环境变量 > `settings.conf` 的 `wizard_admin_port` > `9091`。
- Windows 上如果 `bash` 解析不到（或被 WSL 占走），显式指定：`MIPROXY_BASH="C:/Program Files/Git/bin/bash.exe"`。
- 想离线调试「内核」页（不连 GitHub）：把 `MIPROXY_KERNEL_API` / `MIPROXY_KERNEL_BASE` 指向本地假接口，例如
  `MIPROXY_KERNEL_API=http://127.0.0.1:8000/repos/MetaCubeX/mihomo MIPROXY_KERNEL_BASE=http://127.0.0.1:8000/dl`。
  这两个是**服务端运维开关**，前端传入的地址一律不接受。

### 5. 改前端注意

管理页静态资源从 `${TRIM_PKGVAR}/ui/admin` 读，不存在才回落到 `${TRIM_APPDEST}/ui/admin`。所以改完 `app/ui/admin/` 后：

```bash
cp -rf app/ui/admin/* "$VAR/ui/admin"/     # 已在 UI_TARGET 部署过的话必须重新复制
# 或者：rm -rf "$VAR/ui/admin"              # 删掉让它回落到安装目录，直接读仓库文件
```

其他静态页（`index.html` 选择页、`zashboard`、`metacubexd`）同理落在 `${TRIM_PKGVAR}/ui/` 下。

### 6. 改完自检（三行命令）

```bash
for f in cmd/main cmd/refresh_sub cmd/*_init cmd/*_callback; do
    bash -n "$f" || echo "❌ 语法错误: $f"      # 注意别把 admin_server.py 喂给 bash -n
done
python3 -m py_compile cmd/admin_server.py                                       # Python 语法
python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$VAR/config.yaml"   # YAML 合法性
```

更细的实现约定（环境变量、字段映射、生命周期顺序、历史坑）见仓库根目录 [`agent.md`](agent.md)。

### 7. 本地打一个 .fpk

在仓库副本里跑，避免把仓库结构改乱（CI 里是在一次性 runner 上原地改的）：

```bash
cp -a . /tmp/miproxy-build && cd /tmp/miproxy-build

rm -rf scripts                   # 开发脚本不进包（CI 里同样会删）
ARCH=x86_64                     # 或 arm64
cp -f app/server/$ARCH/mihomo app/server/mihomo
rm -rf app/server/x86_64 app/server/arm64

curl -fsSL -o fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64
chmod +x fnpack
./fnpack build --directory .
ls *.fpk                        # 产物，直接丢进 fnOS 应用中心安装
```

> `fnpack build --directory .` 是**整目录打包**，新增文件（脚本、页面、文档）会自动进包，不需要维护文件清单。唯一需要人维护的是下面"坑 2"里的 chmod / CRLF 清单。

### 8. 本地调试常见坑

1. **`bash` 指向了 WSL**：Windows 上 `where bash` 可能先是 `C:\Windows\System32\bash.exe`（WSL 启动器），它拿不到 Windows 路径语义，脚本会莫名报错。用 `MIPROXY_BASH` 显式指到 Git Bash。
2. **新增 `cmd/` 脚本必须加进 CI 的 chmod 清单**——见下一章"坑 1"。否则本地跑得好好的，装到 NAS 上直接 `Permission denied` 或 `bad interpreter: /bin/bash^M`。
3. **别在 Windows 上用 `os.kill(pid, 0)` 探测进程存活**：Windows 下这不是"发 0 号信号探测"，会**真的终止进程**。`admin_server.py` 已改用 `ctypes` + `OpenProcess`/`GetExitCodeProcess`，请勿改回去。
4. **`tasklist` 又慢又是 GBK**：单次 5–6 秒，且输出是 GBK 会让 `text=True` 抛 `UnicodeDecodeError`。所有 `subprocess` 调用统一 `encoding="utf-8", errors="replace"`。
5. **起服务前先查端口**：上一个自己（或并行会话）留下的进程会占住端口，表现成"代码改了但行为没变"。`Get-NetTCPConnection -LocalPort 9091` → `Stop-Process` 按 PID 清理。
6. **`ICON_256.PNG` 的大小写假 diff**：Windows 文件系统不区分大小写，`git status` 里可能出现"改了但内容完全一样"的图标，**不要提交**。

---

## 构建与发布（GitHub Actions）

仓库有两个工作流，职责不同：

| 工作流 | 触发 | 作用 |
|--------|------|------|
| `.github/workflows/build-release.yml` | 推 `v*` tag / 手动 | 打双架构 `.fpk` + 建**草稿** Release |
| `.github/workflows/sync-binaries.yml` | 每天定时 / 手动 | 同步 mihomo 内核、GEO 数据库、两个面板并自动提交 |

### 1. 日常提交（不会触发构建）

```bash
git add -A
git commit -m "feat: xxx"
git push origin main        # 只推代码，不出包
```

### 2. 方式 A：打 tag 发版（推荐）

```bash
# ① 先把版本号写进 manifest（构建产物里的版本最终以它 / tag 为准）
#    version               = 2.8.1
git add manifest
git commit -m "chore: bump version to 2.8.1"
git push origin main

# ② 打 tag 并推送 —— 这一步才会触发构建
git tag v2.8.1
git push origin v2.8.1
```

版本号解析优先级：**手动输入的 `version` > tag 名（去掉 `v`）> `manifest` 里的值**。所以打 tag 时请让 tag 与 `manifest` 一致，否则源码里的版本号会落后于产物，下次手动构建容易退版本。

> ⚠️ **fork 出来的仓库，推 tag 不会触发构建。** GitHub 默认不在 fork 上运行工作流（Actions 页顶部会提示 "Workflows aren't being run on this repository"）。表现是：`git push origin v2.8.0` 成功、`git ls-remote --tags` 也能看到 tag，但 Actions 运行记录里一条都没有、`total_count` 为 0，而 `workflow_dispatch`（方式 B）却照样能跑。二选一：
>
> 1. 打开 `https://github.com/<owner>/<repo>/actions`，点 **I understand my workflows, go ahead and enable them**，之后 tag 推送恢复正常；
> 2. 直接用方式 B 手动触发，或走 API（注意 `inputs` 为空对象也必须带上，否则返回 422）：
>
> ```bash
> curl -X POST \
>   -H "Authorization: Bearer $GITHUB_TOKEN" \
>   -H "Accept: application/vnd.github+json" \
>   https://api.github.com/repos/<owner>/<repo>/actions/workflows/build-release.yml/dispatches \
>   -d '{"ref":"main","inputs":{"version":"2.8.4"}}'
> ```
>
> 用 API 触发时请让 `version` 与 tag 保持一致。另外若 tag 已经指向 HEAD，`changes` 作业的 `git diff` 为空，Release 正文的「变更详情」会是空白，需要手工补写。

### 3. 方式 B：手动触发（不打 tag 也出包）

GitHub 仓库页 → **Actions** → 左侧 **Build FPK** → **Run workflow**：

- `version`：留空则用 `manifest` 里的版本；
- `create_release`：是否建 Release。⚠️ 该选项在当前工作流里**尚未真正生效**——`release` 作业总会执行（建的是草稿，删掉即可）。

适合"只想拿个安装包验证一下"的场景。

### 4. 流水线做了什么

```
changes ──┬─→ build-x86 ──┐
          └─→ build-arm ──┴─→ release（草稿）
```

1. **changes**：读 `manifest` 定版本号，用 `git describe` 找上一个 tag，生成"变更文件 + 提交历史"摘要。
2. **build-x86 / build-arm**（并行，各 30 分钟超时）：
   - 修复脚本权限：逐个 `sed -i 's/\r$//'` 去 CRLF + `chmod +x`；
   - 下载 `fnpack-1.2.3-linux-amd64` 到 `/tmp/fnpack`；
   - **删掉另一个架构的目录**，把 `app/server/<arch>/mihomo` 移到 `app/server/mihomo`；
   - `sed` 改写 `manifest` 的 `version` 与 `platform`（x86 / arm）；
   - `fnpack build --directory .`，产物重命名为 `miproxy-<ver>-x86_64.fpk` / `-arm64.fpk`；
   - 上传 Artifact（保留 **7 天**）。
3. **release**：下载两个 Artifact，用 `softprops/action-gh-release` 建 Release，`draft: true`，正文含变更摘要与下载表格。

### 5. 产物在哪、怎么用

- **Artifacts**：Actions 运行详情页底部，`miproxy-x86_64` / `miproxy-arm64`，7 天后自动清理（要长期留存请发布 Release）。
- **Release**：为**草稿**状态，需到 **Releases** 页面点 **Publish release** 才对普通用户可见。
- **安装**：把对应架构的 `.fpk` 上传到 fnOS 应用中心手动安装。x86_64 用于 Intel/AMD NAS，arm64 用于 ARM NAS。

### 6. 二进制自动同步（sync-binaries.yml）

- 每天 `02:00 UTC`（北京时间 10:00）自动跑；GitHub 定时任务可能有几分钟到数十分钟延迟。
- 同步内容：mihomo（amd64 / arm64）、`geoip.dat` / `geosite.dat`（Loyalsoldier/v2ray-rules-dat）、`Country.mmdb`（Loyalsoldier/geoip）、Zashboard、MetaCubeXD。
- 手动触发时可勾 `skip_mihomo` / `skip_geo` / `skip_ui` 跳过某类。
- 有变化时以 `🔄 自动同步二进制文件` 提交并直接 `git push origin main`，需要仓库开启写权限：**Settings → Actions → General → Workflow permissions → Read and write**。
- 带体积校验：`geoip.dat` / `Country.mmdb` 小于 1MB 视为下载失败，防止坏文件进库。
- ⚠️ **它只更新仓库里的二进制，不会自动出新包**。同步完想发版，仍要打 tag 或手动跑 Build FPK。

### 7. 改代码时最容易踩的坑

1. **新增 `cmd/` 脚本要加进 chmod/CRLF 清单**：`build-release.yml` 里 `修复脚本权限` 步骤有两个（x86 / arm 各一份），硬编码了脚本列表。新增脚本必须同时加到**两处**，否则装到 NAS 上会因缺执行权限或 CRLF 行尾而失败。
2. **`app/server/x86_64` 与 `arm64` 只放内核**：构建时会 `rm -rf` 掉其中一个并把 `mihomo` 挪到 `app/server/mihomo`，别往里塞其他东西。
3. **版本号以 `manifest` 为准**：CI 用 `sed` 覆盖 `version` 与 `platform`，改版本请改 `manifest`，不要只改 tag。
4. **别提交构建产物**：`.fpk`、`__pycache__/`、`.workbuddy/` 已在 `.gitignore` 中，`git add -A` 前扫一眼 `git status`。
5. **`wizard/config` 里的版本文案**是独立的硬编码字符串，升版本时容易漏改（不影响功能，只影响配置向导里显示的说明文字）。
6. **`scripts/` 会被整目录打进包**：`fnpack build --directory .` 不认排除规则，所以 CI 的两个构建作业都会先 `rm -rf scripts`；本地打包也别忘了删（见上一节）。

---

## 安全与免责

- 本项目仅供学习交流，请遵守当地法律法规及所在网络的使用条款。
- 订阅链接、面板密钥属于敏感信息，保存在 `settings.conf`（仅本机可读），请勿外泄。
- **未设置面板密钥时，管理接口只做只读**：所有写操作（保存配置、刷新订阅、内核开关/升级/回滚、改下载源）一律返回 403。原因是管理服务监听 `0.0.0.0`，没有密钥就没有任何鉴权，而「内核」页会下载/读取二进制并以 root 运行——那等于把 root 执行权交给了整个局域网。请务必设置面板密钥。
- **管理端口不要暴露到公网**：面板密钥就是唯一防线，长期暴露建议只在内网使用或加一层反向代理鉴权。
- 使用公共/第三方节点存在隐私与合规风险，请自行评估。
- TUN 模式会接管整机/局域网流量，启用前请确认理解其影响。
- **内核升级会下载并执行外部二进制**：下载地址只能由官方 Releases 或你填写的镜像前缀推导，不接受任意 URL；镜像前缀请只用可信服务。升级需要 root 权限（`run-as: root`），如无必要请勿在公网暴露管理端口。

---

## 更新日志

### v2.8.4
- 🐛 修复（重要）：**下载订阅时没带 User-Agent，导致「订阅成功但节点为 0」**。绝大多数订阅服务按 UA 决定返回格式，普通客户端（curl 的默认 UA）拿回来的是 **base64 编码的 v2ray 链接**而不是 YAML。现在抓取订阅一律带 `clash.meta` UA，若拿到的内容不像 Clash YAML 会自动换一个 UA 重试一次（`cmd/sub_download.sh`，安装 / 配置变更 / 定时刷新三处统一使用）
- 🐛 修复（重要）：**订阅启用着却一个节点都没解析出来时，不再当成功**。现在会判定失败并说明原因（base64 / 返回的是网页 / 缺少 `proxies:` 段），并且**保留原来那份还能用的配置**，不会用空配置把现有节点洗掉；管理界面把这段诊断原话直接显示出来
- 🐛 修复：**改配置时管理服务被自己杀掉**。`config_callback` 重启内核前会连带 `main stop`，把管理服务也停掉——用户正开着的页面当场断线，看起来像「应用崩了，要手动启动」；而 fnOS 向导触发的那条路径根本不会带上 `MIPROXY_KEEP_ADMIN`。现在 `config_callback` 自身无条件保留管理服务，内核重启与管理服务彻底解耦
- ✨ 面板返回入口改为**页面顶部的导航行**（此前是左下角悬浮按钮，会压住面板自己的操作元素）：两个第三方面板改由带顶栏的包装页承载（iframe），顶栏与面板各占各的版面，面板按自己的视口高度渲染，一点遮挡都没有；也不再修改第三方面板的任何文件

### v2.8.3
- ✨ **两个第三方面板也能返回了**：Zashboard / MetaCubeXD 左下角新增「‹ 面板」悬浮按钮，一点回到面板选择页（此前只有自带管理页有「← 面板」，进到这两个面板里就只能手敲地址栏回去）。按钮由 `install_callback` 在安装时注入到数据目录里的面板 `index.html`，片段单独放在 `app/ui/panel-back.html`：样式内联、无外部依赖、带标记可重复执行，面板自身升级不会被它影响，也不会叠加出两个

### v2.8.2
- 🐛 修复（重要）：**安装/升级完保存配置报「找不到 config_callback 脚本」**。fnOS 会先在 `/vol1/appcenter-downloads/<包名>` 解包并从中执行安装回调，回调拉起 `main`、`main` 再拉起管理服务，于是管理服务的脚本目录指向那个**装完就会被清理**的临时目录；进程还在，页面照常显示，但一切脚本查找都失败——表现正是「页面正常、一保存就报错」，而 v2.8.1 之前它还会被误当成「配置没生效」。现在运行期脚本（`main` / `admin_server.py` / `config_callback` / `refresh_sub`）在每次启动时比对同步到 `/vol1/@appdata/miproxy/cmd/`，管理服务优先从那里启动；同时脚本查找改为依次尝试「数据目录 → 安装目录 → `/var/apps` → `/var/packages` → 脚本自身目录」，不再只认 `__file__` 的兄弟目录
- 🐛 修复：找不到脚本时的报错只给一个路径，看不出问题在哪。现在会把**查过的所有目录**列出来，并直接给出「重启应用」的处置建议（`/api/settings` / `/api/refresh` 返回体里还带 `searched` 数组）

### v2.8.1
- 🐛 修复：**「保存显示成功、配置其实没生效」**。`config_callback` 过去无论内核重启成功与否都 `exit 0`，而 `/api/settings`、`/api/subscriptions` 又是 HTTP 200 + `ok:false`，前端只看 HTTP 状态就把失败当成功提示了。现在 `config_callback` 在内核重启失败时返回 **3**，接口改为返回 **500** 并带上 `config_updated` / `kernel_restarted` / `returncode` 三个独立事实，页面上直接说清「配置写没写进去」「内核有没有换进程」，失败时把 `info.log` 尾部摊在订阅页里
- 🐛 修复：启用订阅后若**一个节点都没提取到**（订阅是 base64 格式、或下载到的是错误页），页面会明确告警，不再静默显示「已生效」
- 🐛 修复：上一次配置还在应用时再点「启用」，单选按钮会停在用户点的位置造成「已启用」的假象；现在会提示并回到服务端真实状态
- 🐛 修复：`/api/settings` 在 `active_sub_id` 与清单对不上时（如另一个标签页刚删掉该条）会**静默把订阅清成 none**，现在沿用当前生效的订阅
- 🐛 修复：在 fnOS 桌面窗口里点击顶部菜单切换不了页面（菜单原本完全依赖 `<a href="#/x">` 的默认导航 + `hashchange`，壳程序拦掉这次导航后 hash 不变、路由永远不触发）。现在**视图切换由 JS 直接驱动**，hash 只做地址栏同步；「连接」按钮刷新当前页也不会再被旧 hash 弹回概览
- ✨ 新增：管理页顶部「← 面板」入口，可一键回到面板选择页（`<mihomo 控制端口>/ui/`，端口由 `/api/status` 的 `panel_port` 下发，不再硬编码 9090）
- ✨ **新增「节点」页**：列出 `config.yaml` 里实际生效的节点（名称 / 类型 / 地址 / 订阅来源），支持按名称、类型、地址搜索、只看可用、单个测速与全部测速（并发 6、单个 4 秒超时、单批上限 300），延迟按 `<200ms` / `<500ms` / 超时分绿 / 橙 / 红，并给出「共 N / 已测 / 可用 / 平均」统计。内核未运行时明确提示无法测速，不做假数据
- 🐛 修复：管理页访问内核控制端口时，密钥取的是**设置项**而非 `config.yaml` 里内核实际在用的 `secret`。改了面板密钥却没重新应用配置时两者不一致，节点页会整页显示 `unauthorized`；现在以内核实际配置为准，读不到才退回设置项

### v2.8.0
- 🔒 **安全修复（重要）**：管理服务在**未设置面板密钥**时完全没有鉴权，而它监听 `0.0.0.0`，且「内核」页会下载/读取二进制并以 root 运行——局域网内任何人可改配置，甚至通过「改下载源 + 安装」组合执行任意二进制。现在**无密钥时只读放行、所有写操作返回 403**，页面顶部给出「去 fnOS 设置里填面板密钥」的引导；密钥校验改用恒定时间比较
- 🐛 修复：卸载前的 `pkill -f mihomo` 会误杀同机其它代理应用（改为只匹配本应用内核路径）
- 🐛 修复：卸载「删除全部数据」缺少路径校验（`TRIM_*` 被误设成卷根目录会删空整卷），现在只删路径含 `miproxy` 的目录
- 🐛 修复：`refresh_sub` 残留锁会让自动刷新**永久静默失效**（现在按 PID 存活 + 30 分钟超时判定并清理）
- 🐛 修复：刷新间隔为 0 或非数字时 `sleep 0` 让守护进程满载空转（间隔下限 60 秒）
- 🐛 修复：`main` 读 `settings.conf` 只裁 `\n` 不裁 `\r`，CRLF 行尾会让「设了远程订阅却不刷新」
- 🐛 修复：升级 `.fpk` 后数据目录里的 `refresh_sub` 旧副本继续生效（原来只在文件缺失时才复制），现在每次比对内容并同步
- 🐛 修复：重新安装会丢失 `wizard_admin_port` / `wizard_admin_token`
- 🐛 修复：内核任务并发竞态（两次「安装」可同时启动）与配置保存/立即刷新并发互相覆盖（现在串行化，冲突返回 409）
- 🐛 修复：管理页在非「概览」页输入面板密钥后，顶部状态一直停在「正在读取状态…」
- ⚡ Windows 本地调试：内核二进制是 ELF 时不再交给 bash 解析（原本会把 62MB 二进制当脚本读，把 `/api/status` 拖到 15 秒超时）
- 🛠️ **新增 `scripts/dev-local.sh` 本地开发辅助脚本**：`init` 一键造开发目录并跑安装回调、`admin` 前台起管理服务、`status` 状态面板、`sync-ui` 同步前端、`restart` / `stop` / `clean`；**不用打包即可直接运行调试**
- 📝 **文档**：新增「本地开发」与「构建与发布（GitHub Actions）」两章——模拟 fnOS 环境跑回调、管理服务本地启动、自检命令、本地打包 `.fpk`、tag/手动触发发版、双架构产物与草稿 Release、二进制每日同步，以及新增脚本必须进 chmod/CRLF 清单等踩坑点
- ✨ **新增内核管理（管理界面「内核」页）**：检查 mihomo 官方新版本、一键升级、按版本切换、恢复安装包内置内核、备份回滚（保留最近 3 个）、下载镜像前缀；切换流程为「下载 → 校验 → 备份 → 替换 → 重启」，失败自动回滚
- ✨ 管理页顶部菜单新增「内核」分页，概览页增加内核版本胶囊
- ✨ **新增在线管理界面（端口 9091）**：订阅（远程/本地/刷新间隔）、代理与面板端口、代理认证、面板密钥、TUN 开关、日志查看与「立即刷新订阅」，均可在页面上修改并即时应用
- ✨ **订阅支持保存多个、单选启用**：管理页可维护多条远程 / 本地订阅（新增 / 编辑 / 删除 / 停用），同一时间只有一个生效；切换启用才重生成配置并重启，单纯增删改不打扰内核
- ✨ 面板选择页新增「MiProxy 管理」卡片，作为管理界面入口
- 🐛 修复：停用订阅（`sub_type=none`）后仍会读取残留的订阅缓存，把旧节点写回 `config.yaml`
- 🐛 修复：安装时未配置订阅、后续再补填订阅不生效（`config_callback` 订阅解析脚本语法错误，导致字段全部为空）
- 🐛 修复：默认生成的 `config.yaml` YAML 格式错误（`proxy-groups` / `dns` 用分号拼行导致解析失败）
- 🐛 修复：配置变更后 TUN 优化项丢失（`stack` 回退为 `system`，缺少 `device` / `strict-route` / `gso`）
- 🐛 修复：`refresh_sub once`（立即刷新）此前只下载不应用；现在会重新生成 `config.yaml` 并重启内核，且支持本地订阅
- 🐛 修复：**定时自动刷新同样只重启不应用**——守护进程过去只替换订阅缓存后重启内核，而节点是内嵌在 `config.yaml` 里的，等于白刷一次。现在固定为「下载 → 重新生成配置 → 重启内核」，并新增 `MIPROXY_NO_RESTART=1` 让 `config_callback` 在刷新链路里只重生成、由守护进程负责重启（否则 `main stop` 会把刷新的发起者自己杀掉）
- 🐛 修复：手动执行 `cmd/config_callback`（不带 `wizard_*` 环境变量）时会把订阅、端口等配置整体清空；现在会回退复用 `settings.conf` 中的现有配置
- 🐛 修复：读取 `settings.conf` 时只裁剪 `\n`，遇到 CRLF 行尾会残留 `\r`，导致 `sub_type` 比较失败、端口带空格
- 🐛 修复：保存配置重写 `settings.conf` 时会丢失 `wizard_admin_port` / `wizard_admin_token`
- 🐛 修复：升级应用包会把用户手动切换过的内核覆盖回内置版本（现在内置版另存 `mihomo.bundled`，仅在缺失时补齐）
- 🐛 修复：`MIPROXY_KEEP_ADMIN=1` 原地重启后可能再拉起第二个管理服务抢同一端口（现在先探测端口是否已在监听）
- 🛡️ 订阅解析失败不再静默：错误写入 `info.log`；缺少 `python3` 时给出明确提示

### v2.7.0
- ⚡ **TUN 配置全面优化**：`strict-route` 防止 DNS 泄露、`gso` 吞吐性能提升、网卡名固定为 `miproxy`
- 🛡️ **新增升级日志文件**：`upgrade_init.log` / `upgrade_callback.log`，升级过程可独立追踪
- 🔧 **日志模块重构**：分离 mihomo 重启日志（写入 `info.log`）与订阅刷新记录（写入 `refresh_sub.log`），避免日志混杂
- 🐛 修复 mihomo 重启日志与订阅刷新日志混写问题
- 🐛 修复 echo 语句语法错误导致的日志记录失败
- 🐛 修复 `refresh_sub` 中重复定义 `MIHOMO_LOG` 变量问题
- 🔄 自动同步 mihomo / 前端最新二进制文件
- 📖 全面重写 README，突出 TUN 全局代理 + NAS 私有网关核心价值

### v2.6.39
- 🛡️ TUN 配置全面优化：新增 `strict-route` 防 DNS 泄露、`gso` 吞吐优化、网卡命名固定为 `miproxy`
- 🐛 修复订阅节点提取失败问题（heredoc bug）
- 🐛 修复 mihomo 二进制路径错误导致启动失败

### v2.6.38
- ✨ 新增控制面板选择页面，安装时自由选择 Zashboard 或 MetaCubeXD

[查看全部版本](https://github.com/56025192/miproxy-fpk/releases)


---

## 技术栈

- **代理内核**：[mihomo](https://github.com/MetaCubeX/mihomo) by MetaCubeX
- **控制面板**：[Zashboard](https://github.com/Zephyruso/zashboard) / [MetaCubeXD](https://github.com/MetaCubexD/MetaCubeXD)
- **管理界面**：原生 HTML/CSS/JS + Python3 标准库 HTTP 服务（`cmd/admin_server.py`，无第三方依赖）
- **运行环境**：飞牛 fnOS ≥ 1.1.0
- **运行依赖**：`python3`（解析订阅 + 管理服务）、`curl`（下载订阅）

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=56025192/miproxy-fpk&type=Date)](https://star-history.com/#56025192/miproxy-fpk&Date)

---

## 许可证

本项目仅供学习交流使用，请遵守当地法律法规。

---

<p align="center">
  <a href="https://github.com/56025192">👤 作者</a> ·
  <a href="https://github.com/56025192/miproxy-fpk/releases">📦 下载</a> ·
  <a href="https://github.com/56025192/miproxy-fpk/issues">🐛 问题反馈</a>
</p>
