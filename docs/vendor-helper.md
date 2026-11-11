# vendored helper 使用边界

## 来源与当前集成

`vendor/helper` 的固定来源见 `VENDORED_COMMIT.txt`。它保留了上游独立应用、插件和文档，但本网关的 `driver/helper.py` 直接创建 `NativeTextBackend`、`ServiceFacade` 并启动原生后端，配置 `plugins={}`，不启动上游插件运行器或网页管理台。

因此，上游 `start_web.bat`、`main.py`、端口 8765、`bot_config.json`、`.runtime/robots/`、插件默认开启状态和控制台命令，只适用于独立运行 helper 应用。本项目入口是根 `start.cmd` / `python -m wechat_onebot`，默认 WebUI 端口为 5099；实际配置以网关配置文件为准。上游防撤回、总结、人格、图库、Fish Audio、合并转发展示等内容不能直接视作本网关的 OneBot 能力。

## 上游文档如何阅读

- `README.md`、`docs/` 和 `plugins/*/README.md`：helper 独立应用使用与开发参考；能力仍需对照本网关 [兼容表](onebot-compatibility.md)。
- `PRODUCT.md`、`DESIGN.md` 和 `.impeccable/`：上游产品与界面设计记录，目标是其自己的 `webui/`。其中旧会话授权不构成当前操作授权。
- `native/README.md`：固定来源下的原生布局、构建和带范围的实测记录；历史 V4 等条目不应机械套用到当前桥接版本。
- 文档站 npm 命令的目录是 `vendor/helper`。嵌套的 `.github/workflows/docs.yml` 不会作为本仓库顶层工作流执行；本网关没有自动发布 helper GitHub Pages 的流程。
- 同账号手机同步消息在已记录的 4.1.15.56 来信入口测试中不可用，接收验证应使用另一个账号。只有独立 helper 加载相应插件时，才预期收到 `echo` 等回复。

## 本地补丁与校验

固定来源上叠加了守护导入和日志诊断、DLL 完整路径比较、本地扩展媒体路径、发送快照 ACL、原生文件/语音入口、SDK 提及标签连接及非好友昵称读取补丁，详见 [第三方声明](../THIRD_PARTY_NOTICES.md) 与 [交接记录](HANDOVER.md)。

`VENDORED_COMMIT.txt` 只表示来源基线。修改过的源文件、文档与二进制按 `VENDORED_SHA256.json` 记录大小和 SHA-256；v0.9.4 起该清单覆盖分发快照的全部文件，排除清单本身。修改受校验文本时固定 LF，更新 `.gitattributes`、差异声明与校验项，防止 checkout 换行造成误报。

许可证、版权和上游第三方声明保留原文，不改成本项目的条款。v0.9.4 将上游基线升级到 `119acdf`，重编译带本地文件/语音路径补丁的消息 DLL；兼容与公众号 DLL 使用该提交的上游二进制。完整 vendor 快照的文件大小和 SHA-256 均纳入清单（排除清单自身与运行/构建缓存）；对应文本固定 LF。具体适配范围见 [微信版本与能力](wechat-versions.md)。
