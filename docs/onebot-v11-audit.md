# OneBot v11 通用性审计

日期：2026-11-04。代码基线：`7b62bdd8742e587a246e046e4130da0be9a26efc`。规范来源：官方 `botuniverse/onebot-11`，固定提交 `d4456ee706f9ada9c2dfde56a2bcfc69752600e4`。原始报告记录该代码基线的差异；当前状态见下方修复映射。

## 2026-11-13 复核修复（v0.11.1）

按 v0.11.0 源码重新核对传输、消息流、事件层后修复六项：

1. **消息事件顺序**：每账号增加 `asyncio.Queue` 串行 worker；驱动入队后按到达顺序入库/上报，不会再因异步补全成员/好友而让后到消息先到。
2. **HTTP 快速操作**：`reply` 与 `delete`/`kick`/`ban`/`approve` 字段独立处理；不支持的动作记录日志，但同一响应中的合法 `reply` 仍会执行。
3. **helper 入站媒体**：helper 已下载完成的语音/视频（`voice_path`/`video_path` 且状态 ready）映射为标准 `record`/`video` 段；未就绪或失败保留文本占位。
4. **撤回通知**：私聊 `friend_recall` 在原始消息已缓存时复用原 `message_id`；群撤回因微信不提供可靠操作者继续抑制。
5. **媒体读取**：新增标准 `get_image`/`get_record`；`get_record` 可把 SILK/WAV 等转为 mp3/amr/wma/m4a/spx/ogg/wav/flac，按内容哈希缓存。
6. **自发消息引用**：驱动返回 native message ID 时登记到发送快照，后续引用该消息会解析为真实发送消息 ID，而不是新建时间戳为 0 的引用快照。

复核后测试回归 **523 passed / 1 skipped**。仍属能力子集：群撤回、群成员进退、出站 `reply`、`@全体`、群管理、原生合并转发卡片等微信不可靠支持的能力继续显式失败或抑制，不伪造事件。原始审计中的 P1-01、P2-01 至 P2-06 仍按下方 v0.9.0 映射保持已修复。

v0.11.2 补充：微信语音条硬上限 60 秒，`record` 超长时降级为普通文件消息；标准 `music` 段按 `audio`/`url` 降级为语音、文件或文本，平台 ID 卡片明确失败。这样标准客户端（如 AstrBot 点歌插件）的长音频发送不再因 60 秒限制整条失败，但“语音”语义在超长时明确变为“文件”。

v0.11.3 补充：WS 单帧上限从 16 MiB 提升为每端点可配置的 `maxMessageBytes`（默认 128 MiB），避免 AstrBot 把长音频转成 `base64://` WAV 后触发 `1009 (message too big)` 和 API 超时；这是传输层容量扩展，不改变消息段语义。

v0.11.4 补充：超长 `record` 不再降级为文件，而是解码为 24 kHz 单声道 SILK 后按 60 秒分段为多条原生语音条；高码率输入在解码时降采样。这样保持“语音进、语音出”的语义，代价是一条 OneBot 消息可能对应多条微信语音消息。

v0.11.5 补充：SDK 桥接启动遇到旧 owner 刚退出留下的 `SESSION_BUSY` 时，等待并重试最多 20 秒，覆盖 watchdog 异步卸载原生 bridge 的窗口；这是运行时生命周期修复，不改变协议字段。


## v0.9.0 修复状态（2026-11-04）

下方原始审计及 JSON 是 `7b62bdd` 的历史证据，保留以便复现，**不再作为当前未修复清单**。本轮核心接口修复映射如下；本地管理按用户确认不要求 OneBot 兼容。

| 原始项 | 当前处理 |
| --- | --- |
| P1-01 合并转发 | 标准 `message` / `node`，旧持久化记录读取时投影；兼容别名可忽略 |
| P2-01 消息输入 | CQ、数组、单对象、`auto_escape`、标准 CQ `raw_message` |
| P2-02 HTTP | GET/POST、query/form/JSON、标准鉴权/请求错误状态、类型转换 |
| P2-03 HTTP POST | `X-Self-ID`、可选签名、快速回复；不支持的操作显式记录错误 |
| P2-04 撤回 | 复用原消息 ID；无法可靠关联的通知不上报 |
| P2-05 媒体 | SQLite 持久化、文件名回发、重启读取、歧义和变更校验 |
| P2-06 字段/语义 | 资料标准未知默认值配 `unavailable_fields`；通知缺少真实必要信息时记录并抑制 |
| 其他 | WS 角色隔离、echo null、异步/限速后缀、分通信生命周期、完整发送快照 |

微信原生缺失的能力不会伪装实现：出站引用、@全体、群管理及合并转发发送仍明确失败。收到的文件用标准文本下载链接、微信贴图用图片表示；历史/能力查询为可选扩展。完整当前支持矩阵见 [兼容说明](onebot-compatibility.md)。本轮不以某个框架宽松解析作为验收条件。

v0.9.2 源码新增 `send_group_forward_msg` / `send_private_forward_msg` 的普通消息转换扩展（16 个标准名称 + 5 个扩展名称），原生合并转发卡片仍未实现。它是用户明确要求的降级发送，返回真实普通消息 IDs 及转换模式，当前契约见 [兼容说明](onebot-compatibility.md#forward-send-conversion-v092)。

以下为原始报告：

## 原始结论与范围

当前实现是 **OneBot v11 的部分实现，加上社区/本网关扩展**，不能保证所有标准客户端不作适配即可使用全部功能。协议运行代码未发现 AstrBot/NoneBot 的包依赖、客户端品牌判断或专有回调调用；但当前转发响应等依赖接入端接受非标准格式，这属于兼容风险，不能以 AstrBot 的成功解析替代标准符合性验收。

检查覆盖动作注册和处理、消息/通知/元事件转换、四种传输适配器、媒体输入输出、富引用、持久化及本地管理边界。使用临时目录、MockDriver、FastAPI TestClient 和 httpx MockTransport 复现；没有启动真实微信、原生注入、真实发送或修改部署。只对标准契约作判断，不承诺已在每种机器人框架中逐项实测。

完整离线复现输出见 [审计证据](audits/2026-11-04-onebot-v11.json)。其中账号、消息 ID、图片和 URL 都是合成夹具，不是运行实例信息。

## 功能边界

| 范围 | 当前情况 |
| --- | --- |
| 普通文本、用户/群 ID、登录号和状态 | 可使用标准字段与通用 JSON 调用；不需要指定品牌接入端 |
| 群原生 @ | 数组输入可用，但仅 @ 链、@全体成员和参数类型仍有限制 |
| 图片和语音发送 | 使用标准 `image` / `record` 载体；媒体文件名回发、重启索引及消费者 URL 可达性须另核对 |
| 入站引用 | `reply` + `get_msg` 属于标准机制；出站 `reply` 目前省略引用效果，只发送其他可发送内容 |
| 合并转发 | `forward` + `get_forward_msg` 名称属于标准；当前响应形状不符合标准，见 P1-01 |
| 链接 | 标题/URL/摘要可由标准文本承载；额外的 `data.link` 非标准，未实现标准 `share` 发送 |
| 文件、微信贴图 | `file` 消息段不在本次 v11 标准段列表内；`face.file` 也不是标准 QQ 表情的 `face.id`，需要扩展或降级 |
| 历史和能力查询 | `get_friend_msg_history`、`get_group_msg_history`、`get_capabilities` 为可选扩展，不应成为标准消息收发前置条件 |
| 仅 @ 开关、账号扫描、桥接、自启、WebUI | 属于网关本地管理，不是 v11 标准 API；不依赖外部机器人框架，但无法宣称全部由标准 OneBot API 管理 |
| 微信原生接入 | 仍依赖固定 helper 及指定微信构建；OneBot 规定对外接口，不取代微信接入实现 |

## 已确认差异

### P1-01：合并转发返回结构依赖兼容解析

标准 `get_forward_msg` 的 `data.message` 必须是 `node` 消息段数组，每段为 `type` / `data` 结构。当前只返回 `messages`，其中每项直接是 `sender` / `content` 对象，外加 `records`、`title`、`status`、`truncated`。因此只读取标准字段的客户端拿不到节点。

依据：[rich_content.py](../src/wechat_onebot/protocol/rich_content.py)、[service.py](../src/wechat_onebot/service.py)。Mock 已确认 `message` 缺失。应先提供标准 `message` 节点，再将兼容别名和诊断信息作为可选扩展；测试必须有只读取标准字段的消费者。

规范：[公开 API](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/api/public.md)、[合并转发自定义节点](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/message/segment.md)。

### P2-01：标准消息输入与 CQ 字符串未完整实现

标准允许消息字符串、数组、单个消息段对象；字符串默认解析 CQ 码，`auto_escape=true` 才按纯文本。当前字符串始终作为文本，单对象返回 `1400`，`auto_escape` 未参与处理。Mock 中 `[CQ:at,qq=...] hello` 返回成功，却只调用 `send_text`。这会改变合法标准请求的含义。

依据：[service.py](../src/wechat_onebot/service.py)。应补齐输入规范化和 CQ 转义规则；不能只用某 SDK 默认生成的数组验收。

规范：[API 参数](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/api/README.md)、[发送 API](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/api/public.md)。

### P2-02：HTTP API 只覆盖 POST JSON 子集

标准支持 GET/POST、query、urlencoded 表单及 JSON。当前只读取 POST JSON：Mock GET `/get_login_info` 为 405，表单调用返回 `1400`。HTTP 错误状态也不同：未知动作、错误 JSON 返回 200；错误 Token 返回 401，而标准分别要求 404、400、403。JSON 中字符串形式的布尔值也未作规范要求的类型转换。

依据：[http_server.py](../src/wechat_onebot/onebot/network/http_server.py)、[actions.py](../src/wechat_onebot/onebot/actions.py)。应统一解析参数并将 HTTP 状态与 WS retcode 分开映射。

规范：[HTTP](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/communication/http.md)。

### P2-03：HTTP POST 上报缺少标准交互

当前上报不带 `X-Self-ID`，也不处理响应 JSON 的快速操作。Mock 接收端返回合法 `reply` 后，驱动没有发送回复。HTTP POST 独立部署因此不能按标准快速回复工作。配置也没有 HMAC `secret` / `X-Signature` 路径；这是可选签名模式的缺口，不意味着未配置 secret 时必须发送签名。

依据：[http_client.py](../src/wechat_onebot/onebot/network/http_client.py)。应保留事件所属账号，处理可实现的快速操作，明确拒绝原生不支持的动作，并补充标准签名配置。

规范：[HTTP POST](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/communication/http-post.md)。

### P2-04：撤回通知不能对应到原消息

正常消息 ID 由 MessageStore 分配，撤回通知却使用独立 `IdMapper("message", ...)` 分配另一 ID。合成实例中原 ID 为 `-289629376`，通知 ID 为 `826648125`，对后者 `get_msg` 返回 `1404`。标准消费者无法将撤回通知关联到已收到消息。

依据：[converters.py](../src/wechat_onebot/protocol/converters.py)。应复用账号/会话内的 native-ID 索引；没有原消息时明确表达不可解析，不能伪造一个看似有效的本地原消息 ID。

规范：[通知事件](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/event/notice.md)。

### P2-05：媒体缓存的通用使用路径不完整

收到图片的 `file` 名称不总能直接回发：注册位于子目录的文件后，发送该名称返回 `1400`，因为解析只在媒体根目录查找。MediaStore 的资源索引只在内存中，新对象打开同一目录后找不到先前 media ID；历史、引用和转发中持久化的旧 URL 可能失效，即使文件仍存在。

依据：[media.py](../src/wechat_onebot/media.py)。应持久化或重建索引，并按可唯一识别的缓存文件名回查资源。该问题与接入端品牌无关，不能依靠客户端自己缓存 URL 掩盖。

规范：[图片/语音文件参数](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/message/segment.md)。

### P2-06：通知与资料存在字段及语义缺口

群成员增减通知目前没有标准 `sub_type`，操作者未知时也省略 `operator_id`。陌生人接口只有 ID/昵称；群成员资料字段不完整，部分路径还可能返回标准角色枚举之外的 `unknown`。其中有些信息来自微信底层的真实缺失，不能通过编造年龄、权限或操作人“补齐”。应逐项决定可用的标准未知值、明确失败或可选扩展，并用严格模型验收。

依据：[converters.py](../src/wechat_onebot/protocol/converters.py)、[service.py](../src/wechat_onebot/service.py)。

## 其他边界与风险

- 数组中的 `at.data.qq` 当前输出为整数；文本段额外带 `data.link` 对象。标准文档说明几乎所有非转发段参数为字符串，严格按字符串参数建模的消费者存在风险。应将标准参数正规化，私有结构放在可忽略的位置。[数组格式](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/message/array.md)
- `raw_message` 只拼接文本，与完整数组消息不等价；只读取原始字符串的应用会丢失 @、媒体和引用信息。[消息事件](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/event/message.md)
- `_async`、`_rate_limited` 后缀未实现；合法 `echo: null` 被省略，而标准要求原样回传 echo。[API 调用](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/api/README.md)、[WebSocket](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/communication/ws.md)
- 反向 WS 固定为 Universal；正向 WS 的配置路径也提供混合服务，没有标准 API/Event 角色分离。Universal 本身是标准模式，并非品牌绑定，但不能据此宣称覆盖全部 WS 模式。[反向 WS](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/communication/ws-reverse.md)
- 生命周期启动向所有适配器广播 `connect`，没有按 HTTP POST 的 `enable/disable` 与 WS 的 `connect` 分别处理。实际网络心跳循环已将 interval 转为整数，不把它误报为浮点间隔问题。[元事件](https://github.com/botuniverse/onebot-11/blob/d4456ee706f9ada9c2dfde56a2bcfc69752600e4/event/meta.md)
- 出站引用被省略、仅 @ 消息链被拒绝、@全体不支持，以及撤回/群管理等缺失，应作为能力子集或显式降级说明，不能称为完整消息语义支持。

## API 名称覆盖

官方公开 API 文档列出 38 个名称；本项目注册 19 个动作，其中 16 个名称属于标准、3 个属于扩展。**名称存在不代表参数、响应和语义全部符合标准**，也不能单凭未实现全部 QQ 能力就推断必须依赖某个框架。

标准名称：`send_private_msg`、`send_group_msg`、`send_msg`、`get_msg`、`get_forward_msg`、`get_login_info`、`get_status`、`get_version_info`、`get_friend_list`、`get_stranger_info`、`get_group_list`、`get_group_info`、`get_group_member_list`、`get_group_member_info`、`can_send_image`、`can_send_record`。

扩展名称：`get_friend_msg_history`、`get_group_msg_history`、`get_capabilities`。`send_group_forward_msg` 和 `send_private_forward_msg` 不在本次官方公开 API 列表中；不能将它们当成 v11 强制接口。

其他标准名称尚未注册，详细列表见证据 JSON。应发布明确支持矩阵；无法在微信上真实完成的操作保持显式失败。

## 后续验收原则

优先修复 P1-01，再补标准消息输入、HTTP/HTTP POST、事件关联和媒体索引。标准返回结构应先成立，兼容别名只作附加项。新增测试应包含不认识 `messages`、`records`、`to_me` 等扩展的严格消费者，并分别验收四种通信路径；AstrBot 等应用的实测作为补充，而非唯一标准。

本轮只更新审计和交接材料，未修改协议实现、运行实例或 Release。
