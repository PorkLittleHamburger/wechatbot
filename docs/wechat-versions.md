# 微信版本与能力

## v0.10.0 适配范围

网关只有一个 `driver.type`，两种真实驱动各自固定一套微信构建，**不能靠修改版本号字符串放行**：

| 驱动 | 固定来源 | 支持的 Windows x64 微信构建 |
| --- | --- | --- |
| `helper` | [helper `119acdf`](https://github.com/Prometheus-first/helper/tree/119acdf9d74654c6ebb8cded2c6841b85af75867) + 本项目补丁 | 4.1.15.13、4.1.15.56、4.2.0.11、4.2.0.12 |
| `sdk` | `wechatbot-api 0.1.0.dev19`（bridge 11 / ABI 2，见 [vendor/wechatbot_api](../vendor/wechatbot_api/README.md)） | 4.2.0.52、4.2.0.53 |

helper 驱动的指纹以 `vendor/helper/native/compatibility.json` 为准；sdk 驱动在
连接前由框架校验 `Weixin.dll` 的完整指纹。**4.2.0.52 与 4.2.0.53 只支持 `sdk` 驱动**；
4.1.15.13 至 4.2.0.12 只支持 `helper` 驱动。其他未列出的构建不会放行。

| Windows x64 微信 | 核心 DLL 字节数 | SHA-256 | 驱动 |
| --- | ---: | --- | --- |
| 4.1.15.13 | 201552944 | `10f8e995453e2da46d4f2b5080cd6da1f13cc5147746adc119ceae38cb039de5` | helper |
| 4.1.15.56 | 204305968 | `e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556` | helper |
| 4.2.0.11 | 205929008 | `745cdf2955fa5128cdb00c056c97571156a5f107406a9bb15ac0754f3246b201` | helper |
| 4.2.0.12 | 205932072 | `2b3677c3f932daaa4ef84c8b2acd0aee12cbe5a779ff9e740c370761e5c291ce` | helper |
| 4.2.0.52 | 204168240 | `d733489eea0b62af77e1aa61ba645cd941583d2a64fb2d7572da818d285f8d84` | sdk |
| 4.2.0.53 | 204154920 | `ac6b174d98ee1b69e95c99a7a39c3197c8c167b7c6d32f5b72c7a7659c104825` | sdk |

sdk 驱动还要求 `wechatbot_api` 包与包内 `wca_bridge.dll` 的版本、大小和哈希
一致；安装请使用 `vendor/wechatbot_api` 中的 wheel，不要单独替换 DLL。

## sdk 驱动能力与边界

框架对 4.2.0.52 / 4.2.0.53 的公开已验证范围决定网关能宣称什么：

- 支持：私聊/群聊文本收发、1–2 人真实缓存名称的群 `@`、PNG 图片与普通文件
  收发、SILK/WAV/MP3/AAC/M4A/OGG/Opus/AMR-NB/WB/FLAC 语音发送（统一转为
  腾讯 SILK v3）、H.264 静音 MP4 视频发送（网关已映射 `video.send`；框架
  标为 filehelper/私聊/群已验证，4.2.0.52 本地私聊短样本实发可播放；其他编码/带音轨
  仍为框架 pending；4.2.0.53 未单独做网关侧视频实发）、入站图片/文件/语音/视频的显式 `request_media` 下载、
  `get_image`/`get_record` 读取收到的媒体（语音可转
  mp3/amr/wma/m4a/spx/ogg/wav/flac）、私聊引用文本/引用原图与两层嵌套转发、
  只读联系人/群/群成员缓存查询。视频不进入转发发送扩展链（仍 1404）。
- 群 `@` 名称：按次 `labels_verified=False`、只读 `mention_label(room, wxid)`
  与 dev18 的自动通知名。网关优先使用当前 SDK 缓存名（verified）；缓存查不到时，
  先取该成员**在本群**自动学到的通知显示名（未验证提交），再只读查询同一 Windows
  用户下其他已登录微信账号的群成员缓存，取群昵称或微信名作为未验证标签（不读私人
  备注、不从其他账号发送），最后才用历史 profile。任意标签即使原生 `completed`
  也不会被接收端识别为真实 @；没有可用真实名时保持可见文本降级。自动名按
  `(账号, 成员, 群)` 隔离，不用于其他成员或其他群，不覆盖全局昵称，wxid 永不作为标签。
  4.2.0.53 的入站通知模板不匹配、多义或为空时不学习；可用
  `driver.sdk_notification_names=false` 关闭学习与出站使用。
- 不支持：表情包发送、撤回、好友申请、群管理、原生合并转发卡片、出站原生引用。
  网关对这些保持明确失败，不返回假成功。
- 群富图片的 `request_media` 在框架内仍为开发 opt-in；网关默认只在私聊场景解析
  引用媒体。设置 `driver.sdk_allow_unverified=true` 只是打开框架的实验开关，
  **不等于已验证能力**。
- 语音入站交付为 WAV（sdk 框架解码后的格式），不是原始 SILK；helper 下载完成的
  语音/视频分别上报标准 `record`/`video` 段，未就绪或失败时保留明确的文本占位。
  视频入站要求安装框架的 video extra（`av==18.1.0`）；语音解码依赖 voice extra。
  `get_record` 可将已收到的 SILK/WAV 等语音转成请求的格式。
- 语音发送在网关侧统一归一化：SILK 原样透传；其他格式由内置 ffmpeg 解码为
  24 kHz 单声道 PCM，再用内置 `silk-python==0.2.9` 编码为腾讯 SILK v3，最后交给
  驱动原生发送。微信语音条单条上限 60 秒：超长音频统一降采样后按 60 秒**分段**
  为多条语音条，不截断、不改发文件；输入文件受媒体大小上限约束，总时长上限
  30 分钟。缓存按输入内容哈希生成，重复发送不重复转码。
- 标准 `music` 发送按媒体降级处理：`custom` 且有 `audio` 时，≤60 秒发一条语音，
  更长音频按 60 秒分段为语音条；没有 `audio` 但有 `url` 时发“标题 + 链接”文本；
  只有平台 ID、无可播放地址时明确失败。
- 联系人/群/成员来自微信本地缓存：`source=wechat_cache`、`complete=False`；
  `LAYOUT_NOT_READY` 表示缓存暂不可读，**不是没有好友或没有成员**。

## 配置

```json
{
  "driver": {
    "type": "sdk",
    "wechat_account": "wxid_xxx",
    "wechat_pid": 12345,
    "sdk_data_root": null,
    "sdk_allow_unverified": false,
    "sdk_cross_account_mentions": true,
    "sdk_notification_names": true,
    "sdk_media_timeout": 30.0
  }
}
```

`wechat_pid` 必须与 `wechat_account` 对应；WebUI 的“进程与账号”页面通过框架的
只读 `discover()` 重新扫描并写回。`sdk_data_root` 默认为账号目录下的 `sdk/`，
用于框架的守护 journal 与私有媒体快照，不要指向共享目录。

## 升级与验收

1. 停止实例，导入完整 v0.10.0 包并切换运行版本，保留配置、账号 SQLite 和媒体目录。
2. 按目标微信构建把 `driver.type` 设为 `helper` 或 `sdk`，确认 `wechat_account`/
   `wechat_pid` 与当前登录进程一致；在 WebUI 中重新扫描一次。
3. 升级或切换微信版本后必须重新核对指纹；相同版本号但不同安装包仍可能不兼容。

v0.10.1 起，旧 `helper` 配置遇到 4.2.0.52 时无需手工改 JSON：启动账号会先按配置驱动
发现，找不到目标账号时自动尝试另一真实驱动，成功后把 `driver.type` 与 PID 写回配置；
WebUI 的账号选择同样支持回退。只有切换成功才写入，失败保留原配置。v0.10.2 起，若配置
驱动在发现阶段命中但原生启动被拒，也会改用另一驱动重试；绑定账号昵称从 SDK 只读联系人
缓存补全，扫描与选择不再用空昵称覆盖已保存的值。

v0.10.3 起，自动回退**不再改写 `driver.type`**：实例模式由 Launcher 拥有，`config.driver.type`
必须与 `instance.json` 的 `mode` 一致；运行时可以临时使用另一驱动，但只持久化账号、PID 和
昵称。这样 Launcher 的启动校验与备份恢复不会因 sdk 类型失败，4.2.0.52 仍可在 `helper`
配置下自动使用 sdk 运行。Launcher 需 **v0.8.12+**（0.8.11 会拒绝 helper 实例中的 sdk 配置）。

本轮在 2026-11-13 进行源码回归（496 项通过、1 项符号链接权限测试跳过）、编译和
便携包构建验证；并用打包后的 Python 执行只读 `discover()`，识别到本机两个已登录的
4.2.0.52 进程（指纹 `d733489e…`、`bridge_supported=true`），Bot 的账号发现映射一致。
经用户授权，另用临时配置/临时数据目录完成连接与启停验收：连接 0.87 秒、状态
`connected`、好友 87/群 4/首个群成员 9、停止 0.50 秒，守护 `WATCHDOG_READY` →
`NORMAL_STOP`；完整 Bot 服务的 WebUI 扫描 → 启动 → `online=true` → 停止同样通过。
**没有发送真实消息、没有修改运行实例配置**；群 `@`、媒体与引用收发仍需真实消息
（入站事件与发送链路尚未验证）。

v0.11.8 将 sdk 框架固定版本升级到 `0.1.0.dev15`（bridge 10 / ABI 1 不变），
并新增实例 WebUI 成员 `@` 显示名绑定。实机对照确认：任意标签即使
`labels_verified=False` 且 native completed，接收端也没有真实 @；真实显示名才有效。
缓存无名称时，手工绑定真实名走按次未验证提交；没有真实名则保持可见文本降级。
**手工绑定名必须与群内实际显示名一致，不能填 wxid 或占位标签。**
（历史能力：该 WebUI 手工绑定入口在 dev18 批次移除，改为自动通知名；见下方“自动通知名”。）

v0.11.9 新增跨账号名称补全：本账号缓存缺失时，sdk 驱动只读查询同机其他
已登录微信账号的群成员缓存，取群昵称/微信名作为未验证标签；默认开启，
`driver.sdk_cross_account_mentions=false` 可关闭。该路径不读其他账号备注，
也不从其他账号发送；其他账号都未缓存该成员时仍需自动通知名或可见文本降级。

## dev17 与 4.2.0.53 双构建（2026-11-18 源码接入，未发布）

`vendor/wechatbot_api` 升级到 `wechatbot-api 0.1.0.dev17`（wheel 122388 字节，
SHA-256 `c1294988…ea0495`；bridge 10 / ABI 1 不变；内嵌 `wca_bridge.dll` SHA-256
`0e8fc3a4…d06693`；构建提交 `c23e83f5…8726f`）。该 wheel 的 `data/builds.json`
同时声明两个 discovery/bridge 已验证的构建：4.2.0.52（`d733489e…f8d84`）与
4.2.0.53（`ac6b174d…104825`）。Bot 不需要改驱动代码：发现账号时由框架按精确
指纹筛选，`Driver.sdk` 继续从 wheel 的构建清单获得支持范围；helper 的
`driver.supported_versions` 仍是 helper-only，**不要**把 4.2.0.53 加进去。

能力口径：4.2.0.53 与 4.2.0.52 共用 sdk 驱动与公开 API；群 `@` 的
`labels_verified=False`/`mention_label` 契约不变，dev17 已适配 4.2.0.53 的目录与
群成员缓存（框架侧证据）。**Bot 未在 4.2.0.53 上发起真实群 `@`**；本仓库仅做
临时配置只读发现与离线 fake SDK 的发送准备路径验证，真实群 `@` 仍需实机确认。
视频发送仅继承 `video.send` 能力，Q-3 的实机可播放证据仍只覆盖
4.2.0.52 私聊短静音 H.264 MP4，**4.2.0.53 未单独做网关侧视频实发**；其他编码/
带音轨仍为框架 pending。本接入未改生产 `config.json`、未启动真实桥接、未发送
微信消息；验证见交接文件。

## dev19 图片诊断与视频依赖（2026-11-20 集成，未发布）

`vendor/wechatbot_api` 升级到 **0.1.0.dev19**（wheel 130249 B，SHA-256
`62de70a9…adef8`；bridge 11 / ABI 2 不变，Python-only）。主要变化：

- 图片 native 下载/严格校验失败时，`OperationResult.detail` 与
  `media_update.reason` 增加脱敏 stage（copy/native_open/byte_count/header/
  PNG CRC/IEND/JPEG EOI/decode/pixels/frames 等），不包含图片字节、
  filekey/aeskey、URL/XML、通知文本或账号群信息；
- 完整 PNG（IEND 后）和完整 JPEG（EOI 后）允许尾部字节；截断、CRC 错、
  缺 IEND/EOI、BMP/HEIC/AVIF、超像素仍拒绝；
- 这不是 dev18 UI 群图片失败的已证实修复；原失败样本尚未重放，不能宣称
  图片问题已解决。

2026-11-21 staging（两个 4.2.0.53 测试账号、`test` 群、各 1 次）：文本、
群文件下载链接、群语音、群视频（安装 `av==18.1.0` 后）、SDK PNG 和用户 UI
手发 PNG 均映射到对应 OneBot 段；UI PNG 本次进入 `image`，不再降级为
`[图片]`。该结果只是单条正向证据；私聊文件/语音、私聊 CDN voice、大图/
HEIC/AVIF 等仍未测试，不能写成 PASS。

Bot 依赖新增 **`av==18.1.0`**（视频接收解码；`requirements-runtime.lock` 与
`pyproject.toml` 同步）。未安装时 SDK 视频接收不能到达 OneBot `video` 段，
只能保留 `[视频]` 占位；生产/Launcher runtime 与 staging 必须统一安装。

## dev18 与 bridge11/ABI2（2026-11-19 源码接入，已随 v0.13.0 发布）

`vendor/wechatbot_api` 从 `0.1.0.dev17` 升级到 **`0.1.0.dev18`**（wheel 124981 字节，
SHA-256 `23b25ff3…c8e7df`；bridge 11 / ABI 2；内嵌 `wca_bridge.dll` 60928 字节、
SHA-256 `fec9c75f…c693db06`；构建提交 `11fd3869…160a4`；sdist SHA-256
`c6b13ca6…9f0935`）。`data/builds.json` 仍声明 4.2.0.52（`d733489e…f8d84`）与
4.2.0.53（`ac6b174d…104825`）两条 discovery/bridge 已验证构建；Bot 驱动代码与
公开 API 调用面不改。

dev18 在原生事件末尾新增 `notification_text`（1024 字节槽，`NativeEvent` 由
528688 扩展到 529712 字节）和只读的 `sender_display_name`；ABI 1 的字段偏移、
`Request`/`AtRequest` 大小与既有能力位不变。4.2.0.52 不读取该槽，两个新字段恒为
`None`；通知名只来自 4.2.0.53 群消息的严格 `@self` 显示名或固定通知模板，不读
缓存/备注/资料。网关的消费者实现见下一节。

本接入未改生产 `config.json`、未启动真实桥接、未发送微信消息、未发布；验证见
交接文件。回滚：恢复 dev17 wheel（备份在 gitignored 的
`dist/sdk-backup-dev17/`）与 `VENDORED.json`/文档，并
`pip install --no-index --no-deps --force-reinstall` 旧 wheel。

## 自动通知名（2026-11-20 源码接入，未发布）

dev18 的只读通知字段被消费为**未验证显示名**，不改变 OneBot 身份映射：

- 学习范围：仅 4.2.0.53 群消息。`@` 本账号的文本消息使用严格
  `sender_display_name`；非 `@self` 消息按 `content_type` 只接受固定模板
  （`<名> : <正文>`、`<名> : [文件]<文件名>`、`<名> : [聊天记录]`、
  `<名>在群聊中发了一张图片/一段语音/一段视频`）。多分隔符、空正文、非模板后缀、
  控制字符、长度 1–64 之外、等于 wxid 的值都不写入。
- 存储：`user_profiles.notification_name` 与 `source='notification'`，键为
  `(account, target_user_id, group_id)`；只写群 card，不创建/覆盖全局昵称行。
  后续 roster/message 刷新改 `source` 但保留该列，因此自动名不会被当成已验证资料。
- 入站：只影响本条事件的 `sender.card`；`sender.user_id`/`sender_id` 与消息 ID 不变。
- 出站群 `@` 优先级：缓存 verified → 目标自己的自动通知名（unverified，
  `labels_verified=False`）→ 跨账号缓存（unverified）→ 历史 profile；A 的自动名
  不用于 B 或其他群；多目标混用 profile 与未验证来源时整组可见文本降级；缺名且
  存在未验证来源时同样降级；wxid 永不作为 native display label。
- 开关：`driver.sdk_notification_names` 默认 `true`。关闭后不提取模板、不写库、
  出站不读自动名，并让 profile fallback 忽略 `card == notification_name` 的旧自动名
  （避免其以 verified profile 身份继续生效）；严格 `@self` 名仍用于入站
  `sender.card` 展示。关闭开关即软回滚，数据保留；清空旧自动名需停服备份 DB 后
  按 `source='notification'` 人工处理。
- 日志：不记录通知原文与显示名，只记录账号、群、来源和错误码；原文不进入
  `IncomingMessage.raw`、消息库或事件 payload。
- 历史 `mention_labels` 表与其数据保留（不读不写、不 DROP）；手动绑定 WebUI 卡片与
  `GET/PUT/DELETE /api/accounts/{account}/mention-labels` 路由已删除（现在返回 404）。
- **出站使用为 experimental（有意偏差）**：dev18 SDK 文档（`wechatbot_api/_notification.py`）
  写明 `sender_display_name` 仅用于入站/展示，*“must never be used as a native outbound
  @ label”*；上游要求可点击 @ 使用接收者的实际显示名，而该字段描述的是发送者。用户
  明确选择保留“同一成员、同一群”的自动名用于后续未验证出站 @，并接受该 experimental
  风险：只用 `labels_verified=False` 按次提交，原生 `completed` 可能仍无真实 @，不得据此
  宣称已验证 @。该用法受上面的目标/群隔离、wxid 过滤、缓存优先与开关约束；关闭
  `driver.sdk_notification_names` 后不学/不写/不用于出站。
