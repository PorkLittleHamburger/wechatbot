# 故障排查

先记录实际 Bot 版本、配置文件位置、`driver.helper_root`、微信核心构建及错误时间。发布版本、源码版本和实例正在使用的版本可能不同；不要仅根据下载了新包判断实例已升级。公开报告前移除 Token、个人账号和聊天内容。

## 守护启动失败与日志

桥接错误会区分提前退出（含退出码）与等待就绪超时，并给出日志绝对路径。实际位置是当前实例的 `<driver.helper_root>/logs/`：

- `bridge_watchdog_startup.log`：启动输出及日志初始化前的导入错误。
- `bridge_watchdog.log`：守护运行日志，提前退出时可能尚未生成。

源码默认 helper 路径为配置文件目录下的 `vendor/helper`；外部 Launcher 可指定另一位置。使用有相应读取权限的终端查看报错给出的绝对路径，不要硬编码其他人的安装目录。isolated/embeddable Python 的导入路径修复位于 `native/bridge_watchdog.py`。

## sdk 驱动（微信 4.2.0.52）

`driver.type="sdk"` 的错误代码来自框架，含义稳定，不要解析异常文本：

- `UNSUPPORTED_BUILD` / “未发现已登录且受支持的微信账号”：确认微信是 4.2.0.52 且核心指纹匹配；4.2.0.52 只能用 `sdk`，其余已适配构建只能用 `helper`。修改 `supported_versions` 或复制 DLL 都不放行。
- `LAYOUT_NOT_READY` / “Core module has not loaded”：微信仍在启动或核心模块未加载；等待登录完成再扫描，枚举到的其他进程也会以该代码出现。
- `ACCESS_DENIED`：Bot 进程权限低于微信；用与微信同级或更高权限的终端启动。
- `SESSION_BUSY`：同一微信进程已有其他 owner 会话；关闭那个进程的 SDK/框架客户端后再启动。
- `SHUTDOWN_PENDING` / `MEDIA_CLEANUP_PENDING`：原生任务或私有文件清理尚未完成。保留会话并等待，不要强制卸载或结束微信进程。
- 发送返回“结果未知”：原生可能已发送。不要自动重发；用返回的 `operation` 或状态页确认，禁止把 `accepted` 当成功。
- 入站媒体显示 `[图片]`/`[语音]` 等占位：检查 `driver.sdk_media_timeout`、框架能力是否支持该场景；群富图片在框架内仍为开发 opt-in，语音/视频接收需要框架的 extras。占位是明确失败，不是已接收。

`get_status` 的 `details.watchdog` 与 `details.wechat_version/sha256` 可确认守护状态和实际构建；框架守护 journal 在账号目录的 `sdk/.guards/` 下，只应读取，不要手工改写或删除正在使用的条目。

## 桥接 DLL 未正确加载

核对错误中的预期路径和实际路径、实例引用的 helper 目录、DLL 是否存在及 `VENDORED_SHA256.json`。`native/run_selftest.py` 和 `native/filehelper_service.py` 会统一比较 Windows 扩展驱动器及 UNC 路径，但其他目录下的同名 DLL 不能冒充当前模块。这里的模块路径比较不表示媒体发送支持 UNC。

原生适配还要求指定微信构建的核心 DLL 指纹匹配。不要通过放宽版本列表或复制未知 DLL 绕过检查。

## 媒体路径与权限

本地文件和语音路径支持 Windows 扩展驱动器前缀。UNC、设备命名空间、URL、备用数据流及映射网络盘不能直接作为 helper 的本地发送路径；OneBot 远程媒体需先经过网关下载与验证。

发送快照会给当前进程用户补充只读/遍历权限，便于普通权限微信读取管理员进程创建的文件；授权失败会中止发送并清理临时目录。入站图片和文件先下载到微信账号自己的临时目录，再复制至账号媒体目录。分别检查原生下载、复制、快照 ACL 及返回值，不能用文件存在代替发送成功。

## 昵称或原生 @ 显示用户 ID

先核对收到的 `sender.nickname`、`sender.card` 与用户/群 ID，区分网关事件、客户端生成的正文和微信显示。收到 @ 标记本身不包含发送者昵称。

资料在事件上报前保存，空值不覆盖已知名称；微信 4.1.15.56、4.2.0.11 和 4.2.0.12 支持从已验证的非好友联系人 LRU 读取昵称。尚未缓存、读取中变化或其他版本没有相应来源时仍可能缺名。更新后用新消息验证，已有聊天记录不会被改写。不要伪造名称或通过修改 ID 映射掩盖问题。

## 同群成员的 UMO 不同

UMO 属于外部客户端的会话语义。AstrBot 的群内隔离对话可把会话 ID 组合为 `用户UID_群ID`，关闭时才通常按群共享。检查消息实际路由到的配置文件及其 `platform_settings.unique_session`，不要仅查看默认配置；比较真正的 OneBot `group_id`。调整隔离还会改变上下文划分，修改后应用配置并用新消息复核。

更完整的协议限制见 [OneBot 兼容说明](onebot-compatibility.md)，真实收发步骤见 [原生桥接验证](live-preflight.md)。

## Launcher 创建 WebView2 窗口失败

若“打开 WebUI”报告 `failed to create webview`、`0x80010108`（对象与客户端断开），错误发生于 Launcher 内嵌 WebView2 窗口层。先通过普通浏览器访问该实例实际配置的 `http://127.0.0.1:<port>/`，并检查 `/healthz`。HTTP 正常时，重打 Bot 包或重新注入微信并不能证明窗口故障已修复，应检查 Launcher 窗口生命周期及 WebView2 运行环境。记录 Launcher 版本、错误和触发步骤，交由 Launcher 项目处理；不要删除账号、SQLite 或媒体数据。
