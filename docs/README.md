# 文档索引

本目录描述当前 Bot 网关。历史提交、已发布 Release 和上游 helper 文档各有自己的适用版本，不作为当前功能清单。

## 使用与集成

| 文档 | 用途 |
| --- | --- |
| [当前目录入口说明](../README.md) | 源码目录中为项目 README；便携包中为运行说明 |
| [源码项目 README](https://github.com/PorkLittleHamburger/wechatbot/blob/main/README.md) | 源码环境安装、测试、构建及下载入口 |
| [便携包说明](../PORTABLE_README.md) | 解压运行、升级、自启和数据迁移；构建时成为包内 README |
| [网络配置](network-config.md) | 账号端点、鉴权、心跳及连接方向 |
| [OneBot 兼容说明](onebot-compatibility.md) | 已实现动作、消息段和外部客户端注意事项 |
| [v11 通用性审计](onebot-v11-audit.md) | 对照官方规范的已知格式、传输及语义差异；不等于已修复 |
| [富消息与引用](rich-messages.md) | 文本、链接、图片、嵌套转发及 AstrBot 读取方式 |
| [故障排查](troubleshooting.md) | 日志定位、桥接路径、媒体权限和昵称缺失 |
| [原生桥接验证](live-preflight.md) | 只读检查与有副作用的真实验证边界 |

## 开发与接手

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | 贡献规则与 AI 必须遵守的文档维护要求 |
| [开发交接](HANDOVER.md) | 接手顺序、代码入口、关键决策、发布流程和待办 |
| [架构](architecture.md) | 当前调用关系、数据所有权和 ID/媒体实现 |
| [架构参考](snowluma-alignment.md) | 设计来源与运行边界，不表示完整实现了参考项目 |
| [后续方向](roadmap.md) | 尚未承诺交付的工作 |
| [本轮文档审计](documentation-audit.md) | 审计范围、证据和修正记录 |
| [helper 边界](vendor-helper.md) | 上游独立应用与本项目的区别 |
| [第三方声明](../THIRD_PARTY_NOTICES.md) | 上游固定来源、许可证和本地修改 |

维护时从代码、配置模板和测试确认行为，再同步相应说明及交接。默认地址和路径均可被配置覆盖；不得将个人部署目录、账号或临时代理写为使用要求。
