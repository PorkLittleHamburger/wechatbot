# 原生微信桥接验证指南

本指南用于在目标环境中检查原生桥接、账号绑定和 OneBot 收发。Mock 验证覆盖协议及管理逻辑，不能代替具体微信构建和账号环境中的真实桥接验证。

## 准备与只读检查

- 使用 Windows x64 和 64 位 Python，确认微信构建在配置及原生适配范围内，账号已在 PC 微信登录。`config.example.json` 当前列出 `4.1.15.13`、`4.1.15.56`、`4.2.0.11`、`4.2.0.12`；列入配置不表示每项缓存读取能力在所有构建上相同。
- 使用专门的测试账号和群，核对目标账号与进程 PID；多账号环境中显式选择目标账号。
- 真实桥接需要管理员权限。配置、日志和测试记录保存在受控位置。

源码环境配置见 [源码仓库快速开始](https://github.com/PorkLittleHamburger/wechatbot/blob/main/README.md#快速开始)。以下命令从源码仓库根目录执行；便携包根 README 是运行说明，不提供完整开发环境：

```powershell
.venv/Scripts/python.exe -m wechat_onebot --config config.json doctor
```

`doctor` 检查平台、Python 位数、管理员权限、已安装微信版本、Helper 文件及已有清单项的哈希、配置占位 Token 等，并输出 JSON。它不启动桥接，也不证明账号在线、网络端点已监听或收发正常。报告中的 `onebot_endpoint` 来自主配置的旧字段，不是账号实际监听端点。退出码为 `0` 表示这些检查通过，`1` 表示未全部通过。

注意：`AppConfig.load` 会在配置不存在时创建配置，并原子重写加载的配置、补齐缺失的 WebUI 字段。因此上述命令不涉及桥接操作，但不是文件系统层面的完全只读检查。自动创建配置使用随机 Token；直接复制示例后须替换其中的占位 Token。

随后在 WebUI“进程与账号”中只读扫描、选择账号并保存绑定；扫描和绑定不等同于启动原生桥接。WebUI 保存的绑定由管理服务使用；直接运行 `live_preflight.py` 则读取所传主配置中的 `driver.wechat_account`、`driver.wechat_pid` 等字段，不会自动选择 WebUI 中的某个账号。脚本没有 `--account` 或 `--pid` 参数，多账号时应先核实专用测试配置。

## 桥接与发送验证

仅在明确需要连接测试账号时，在 WebUI 手动启动该账号，检查桥接状态、账号标识、进程 PID 和日志。

验证时可依次检查：

1. 登录信息、好友和群信息是否属于目标账号。
2. 向文件传输助手或指定测试会话发送一条文本，核对接收端、返回消息 ID 和本地消息记录。
3. 使用实际配置的 HTTP、正向 WebSocket 或反向 WebSocket 客户端检查请求、响应、生命周期和心跳事件。
4. 分别停止桥接、管理服务并检查状态：仅停止桥接时账号网络端点继续运行并报告离线；停止管理服务后再检查所属监听端口和桥接资源是否清理，不要求退出 PC 微信。

开发环境还提供显式发送测试脚本：

```powershell
.venv/Scripts/python.exe scripts/live_preflight.py --config config.json --send-filehelper
```

该命令要求 `driver.type=helper`，会加载原生桥接并向文件传输助手发送测试消息；仅在已确认的测试环境中执行。省略 `--send-filehelper` 不会执行该文本发送，但仍会尝试连接原生桥接。脚本只支持 `--config`（默认 `config.json`）和 `--send-filehelper` 两个业务参数。不要与占用同一账号/PID 的桥接并行运行。

该脚本直接测试 `HelperDriver`，不启动 OneBot HTTP/WS 服务，也不经过 OneBot 的 ID 分配和消息存储。它输出登录、状态、好友数/读取错误、群数/读取错误，以及启用发送时的 `filehelper_send.accepted`。退出码 `0` 只取决于启动后取到的 `status.online` 和 `status.good`，即使好友读取失败或 `accepted=false` 也可能返回 `0`；不健康时返回 `2`，未捕获异常会使脚本失败。必须另查发送结果和接收端，不能仅以退出码认定发送成功。

其他开发预检入口的范围如下；先审阅对应脚本，再按所需范围选用，不能把脚本名当作无副作用的检查保证：

| 入口 | 参数与行为 | 限制 |
|---|---|---|
| `scripts/live_runtime_preflight.py` | 仅 `--config`；启动账号 Runtime 并检查 HTTP、正向 WS | 需各有一个已启用端点；HTTP 检查固定请求 `/`，因此用于该检查的 HTTP 端点须配置为 `/`，绑定地址也须可供本机客户端连接；不主动发送微信消息 |
| `scripts/live_full_stack_preflight.py` | 仅 `--config`；启动 Runtime 并通过 OneBot service 向文件传输助手发送文本 | 没有发送开关，执行即尝试发送；不是 HTTP/WS 客户端测试 |
| `scripts/live_webui_preflight.py` | 仅 `--config`；临时启动 WebUI 并请求 `/healthz` | 管理服务启动流程可能恢复已保存的账号网络；不覆盖登录、原生收发或完整 WebUI 操作 |
| `scripts/run_live_preflight_elevated.ps1` | 无参数；使用仓库 `.venv`、`config.json`，记录到 `data/` | 仅检查当前会话是否管理员，不自行提权；固定附加 `--send-filehelper`，执行即尝试发送 |

新账号默认没有 OneBot 端点；测试 HTTP/WS 前需在该账号网络配置中显式启用端点，地址及凭据以实际保存值为准，见[网络配置](network-config.md)。

## 入站与媒体验证

由另一个测试账号向目标账号发送私聊文本、图片、文件和包含微信原生群 `@` 的消息，核对：

- 私聊、群聊和发送者标识正确，并映射为稳定的 OneBot 数字 ID。
- 文本生成 `text` 段，微信原生 `@` 生成 `at` 段；手动输入的普通 `@名称` 不视为原生提及。
- 图片与文件完成实际下载后，核对媒体段、真实文件及消费者可访问的 HTTP 地址。缺少路径时可能产生 `[图片]`、`[文件或卡片]` 文本占位；媒体注册失败也可能保留原始路径并给出空 `url`，这些都不能记为成功接收。仅配置 WS 不会提供 HTTP 媒体服务。
- 媒体保存在 `storage.media_dir/<safe-account-id>/`（默认 `data/media/<safe-account-id>/`），与账号状态目录 `storage.data_dir/accounts/<safe-account-id>/` 分开；核对文件类型和内容。
- 中断连接、重启管理服务和多个账号并行时，不会串用账号、凭据或数据目录。

## 能力与真实性核对

- 用已映射的数字 `user_id` 检查 `get_stranger_info`，区分未知映射（`1400`）、无已观测资料（`1404`）及已观测但昵称为空。`no_cache=true` 只尝试刷新好友缓存，不是非好友联网查询；参数须用 JSON 布尔值。
- 发群 `at` 时使用消息段数组、已映射的 `data.qq` 和非空文本，接收端确认微信原生提及实际生效。普通字符串中的 `@名称` 不能代替原生提及，`qq="all"` 不支持。出站 `reply` 会使整条消息明确失败，不能按普通发送成功记账；需要普通回复时显式提交不含该段的消息链。
- 群消息的 `sender.role=member` 可能来自未知角色的兼容默认值，不可据此证明真实权限。成员查询应区分原生角色、`unknown`、以及仅凭观测成员资料时省略的角色字段。
- 检查未注册动作（例如 `delete_msg`、`set_group_ban`）返回 `status=failed`、`retcode=1404`，不把已注册处理器列表当作所有原生能力均可用的保证。

完整边界见 [OneBot 兼容性](onebot-compatibility.md)。源码及离线回归依据包括
[`live_preflight.py`](https://github.com/PorkLittleHamburger/wechatbot/blob/main/scripts/live_preflight.py)、
[`cli.py`](../src/wechat_onebot/cli.py)、
[`test_service.py`](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_service.py)、
[`test_user_profiles.py`](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_user_profiles.py)、
[`test_network_manager.py`](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_network_manager.py) 和
[`test_helper_conversion.py`](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_helper_conversion.py)。便携包不包含这些开发测试和预检脚本，链接指向源码仓库。

## 结果记录

记录应用版本、微信构建、Windows 版本、驱动、测试端点及每项结果。对未覆盖的媒体、客户端、长时间重连和异常退出场景明确标记未验证。

公开问题报告仅附去除凭据、消息正文及个人账号标识后的日志。具体环境的一次验证结果不代表其他版本或设备均兼容。
