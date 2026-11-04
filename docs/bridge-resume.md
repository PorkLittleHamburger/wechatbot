# 桥接自动恢复

本文描述 v0.9.3 的桥接恢复能力。管理服务、OneBot 端点与微信桥接有各自的生命周期；管理服务健康不代表桥接已恢复。

## 启动与停止

独立运行通过 `run --resume-bridge` 请求恢复；Windows 自启脚本的 `-AutoResume` 会传入此参数。普通 `start.cmd` 仍只启动管理服务。

恢复目标来自实际 `storage.data_dir/startup.json` 的 `last_account`。成功手动启动桥接会保存目标及 `desired: "running"`；旧版仅有 `last_account` 的文件按 `running` 读取。没有目标时报告 `no_target`，不从配置或登录列表猜测账号。

| 条件 | 行为 |
| --- | --- |
| 目标账号已登录 | 唯一匹配账号，启动前重新扫描当前 PID，然后启动桥接 |
| 微信未启动或目标账号未登录 | `waiting_login`，按间隔只读扫描；识别到目标后启动 |
| 只有其他账号登录 | 继续等待指定目标 |
| 目标存在多个匹配 | `failed`，不选择任意进程 |
| 手动停止 | 取消该账号等待，保存 `desired: "stopped"`；后续自启不越过停止意图 |
| 正常退出服务 | 排空已开始的启动/停止工作，再释放桥接；保留持久化意图 |

需要重新允许恢复时，手动成功启动该账号桥接。等待或自动启动期间可在账号页点击“停止微信桥接”。已进入原生初始化的工作不会被取消等待后遗留在后台；停止请求等它结束，再停止桥接。数据目录无法写入时仍执行本次停止，但明确警告无法保证下次启动的持久化意图。

只有启动前二次扫描发现目标暂时离线时返回等待。扫描异常、桥接初始化异常及非唯一匹配明确报告失败，不每秒重复注入。排除错误后可手动启动；受信任的宿主也可显式重试恢复。

## Launcher 集成契约

Launcher 负责恢复开关、开机启动意图及运行版本兼容检查；Bot 是唯一的账号扫描、恢复任务和桥接生命周期所有者。不要由 Launcher 再建立账号扫描或第二套启动重试。

- 能力常量：`wechat_onebot.webui.bridge_resume.RESUME_CAPABILITY_VERSION == 1`。
- 宿主通过 `create_webui_app(..., start_runtime=True)` 显式请求启动恢复。普通管理启动、升级验证和备份还原不应隐式打开此参数。
- 创建应用后可读取 `app.state.bridge_resume.snapshot()`；WebUI 的鉴权 `/api/status` 及状态流同时包含 `bridgeResume`。这属于本地管理接口，不是 OneBot 协议事件。
- 快照包含 `supported`、`version`、`enabled`、`account`、`desired`、`state`、`error`。状态为 `disabled`、`no_target`、`waiting_login`、`starting`、`running`、`stopping`、`stopped`、`failed`。`enabled` 表示本进程收到恢复请求，不能代替 `state == "running"`。
- `await app.state.bridge_resume.resume()` 是受信任进程内的幂等请求，尊重已保存的停止意图；不提供未鉴权 HTTP 启动入口。关闭由 WebUI lifespan 负责。

Launcher 必须检查实际所选运行版本中的 worker 和 Bot 能力，不能只检查 Launcher 本体版本或 `/healthz`。旧 worker 可能固定禁用恢复并忽略附加参数；没有能力或恢复状态握手时明确提示升级/重新导入兼容运行包，不报告桥接恢复成功，不原地改写已安装版本。

## 验证边界

离线测试覆盖已登录、延迟登录、其他账号、手动停止与重启、初始化中停止/关机、HTTP 等待取消、失败及持久化警告。Mock 与纯文件测试不代替 Windows 登录自启及真实微信桥接验收；发布或更新 Launcher 本体也不会自动切换已有实例的 Bot 版本。
