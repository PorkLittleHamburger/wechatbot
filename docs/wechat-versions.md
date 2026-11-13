# 微信版本与能力

## v0.10.0 适配范围

网关只有一个 `driver.type`，两种真实驱动各自固定一套微信构建，**不能靠修改版本号字符串放行**：

| 驱动 | 固定来源 | 支持的 Windows x64 微信构建 |
| --- | --- | --- |
| `helper` | [helper `119acdf`](https://github.com/Prometheus-first/helper/tree/119acdf9d74654c6ebb8cded2c6841b85af75867) + 本项目补丁 | 4.1.15.13、4.1.15.56、4.2.0.11、4.2.0.12 |
| `sdk` | `wechatbot-api 0.1.0.dev14`（bridge 10 / ABI 1，见 [vendor/wechatbot_api](../vendor/wechatbot_api/README.md)） | 4.2.0.52 |

helper 驱动的指纹以 `vendor/helper/native/compatibility.json` 为准；sdk 驱动在
连接前由框架校验 `Weixin.dll` 的完整指纹。**4.2.0.52 只支持 `sdk` 驱动**；
4.1.15.13 至 4.2.0.12 只支持 `helper` 驱动。其他未列出的构建不会放行。

| Windows x64 微信 | 核心 DLL 字节数 | SHA-256 | 驱动 |
| --- | ---: | --- | --- |
| 4.1.15.13 | 201552944 | `10f8e995453e2da46d4f2b5080cd6da1f13cc5147746adc119ceae38cb039de5` | helper |
| 4.1.15.56 | 204305968 | `e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556` | helper |
| 4.2.0.11 | 205929008 | `745cdf2955fa5128cdb00c056c97571156a5f107406a9bb15ac0754f3246b201` | helper |
| 4.2.0.12 | 205932072 | `2b3677c3f932daaa4ef84c8b2acd0aee12cbe5a779ff9e740c370761e5c291ce` | helper |
| 4.2.0.52 | 204168240 | `d733489eea0b62af77e1aa61ba645cd941583d2a64fb2d7572da818d285f8d84` | sdk |

sdk 驱动还要求 `wechatbot_api` 包与包内 `wca_bridge.dll` 的版本、大小和哈希
一致；安装请使用 `vendor/wechatbot_api` 中的 wheel，不要单独替换 DLL。

## sdk 驱动能力与边界

框架对 4.2.0.52 的公开已验证范围决定网关能宣称什么：

- 支持：私聊/群聊文本收发、1–2 人真实缓存名称的群 `@`、PNG 图片与普通文件
  收发、WAV/受支持 SILK 语音发送、H.264 静音视频发送、入站图片/文件/语音/视频
  的显式 `request_media` 下载、私聊引用文本/引用原图与两层嵌套转发、只读联系人/
  群/群成员缓存查询。
- 不支持：表情包发送、撤回、好友申请、群管理、原生合并转发卡片、出站原生引用。
  网关对这些保持明确失败，不返回假成功。
- 群富图片的 `request_media` 在框架内仍为开发 opt-in；网关默认只在私聊场景解析
  引用媒体。设置 `driver.sdk_allow_unverified=true` 只是打开框架的实验开关，
  **不等于已验证能力**。
- 语音入站交付为 WAV（框架解码后的格式），不是原始 SILK。视频入站要求安装
  框架的 video extra（`av==18.1.0`）；语音解码依赖 voice extra。
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
WebUI 的账号选择同样支持回退。只有切换成功才写入，失败保留原配置。

本轮在 2026-11-13 进行源码回归（496 项通过、1 项符号链接权限测试跳过）、编译和
便携包构建验证；并用打包后的 Python 执行只读 `discover()`，识别到本机两个已登录的
4.2.0.52 进程（指纹 `d733489e…`、`bridge_supported=true`），Bot 的账号发现映射一致。
经用户授权，另用临时配置/临时数据目录完成连接与启停验收：连接 0.87 秒、状态
`connected`、好友 87/群 4/首个群成员 9、停止 0.50 秒，守护 `WATCHDOG_READY` →
`NORMAL_STOP`；完整 Bot 服务的 WebUI 扫描 → 启动 → `online=true` → 停止同样通过。
**没有发送真实消息、没有修改运行实例配置**；群 `@`、媒体与引用收发仍需真实消息
（入站事件与发送链路尚未验证）。
