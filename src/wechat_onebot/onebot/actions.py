from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ActionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParamSpec:
    kind: str = "any"
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class ActionDefinition:
    name: str
    handler: str
    summary: str
    params: dict[str, ParamSpec]
    returns: dict[str, Any]
    examples: tuple[dict[str, Any], ...] = ()
    read_only: bool = False

    def normalize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = dict(params)
        for name, spec in self.params.items():
            value = result.get(name)
            if spec.kind == "bool" and isinstance(value, str):
                if value.lower() in ("true", "yes", "1"):
                    result[name] = True
                elif value.lower() in ("false", "no", "0"):
                    result[name] = False
            elif spec.kind == "str" and isinstance(value, (int, float)) and not isinstance(value, bool):
                result[name] = str(value)
        self.validate(result)
        return result

    def validate(self, params: dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise ActionValidationError("params 必须是 JSON 对象")
        for name, spec in self.params.items():
            if spec.required and name not in params:
                raise ActionValidationError(f"缺少参数: {name}")
            if name not in params:
                continue
            value = params[name]
            if value is None and not spec.required:
                continue
            if spec.kind == "str" and not isinstance(value, str):
                raise ActionValidationError(f"{name} 必须是字符串")
            if spec.kind == "int":
                if isinstance(value, bool) or not (
                    isinstance(value, int) or (isinstance(value, str) and value.isascii() and value.isdecimal())
                ):
                    raise ActionValidationError(f"{name} 必须是整数或数字字符串")
            if spec.kind == "signed_int32":
                if isinstance(value, str) and value.isascii() and value.startswith("-"):
                    normalized = value[1:]
                else:
                    normalized = value
                valid_number = isinstance(value, int) or (isinstance(normalized, str) and normalized.isdecimal())
                number = int(value) if valid_number and not isinstance(value, bool) else 0
                if isinstance(value, bool) or not valid_number or number == 0 or not -(2**31) <= number <= 2**31 - 1:
                    raise ActionValidationError(f"{name} 必须是非零 signed int32")
            if spec.kind == "bool" and type(value) is not bool:
                raise ActionValidationError(f"{name} 必须是布尔值")

    def public_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "read_only": self.read_only,
            "params": {
                name: {"kind": spec.kind, "required": spec.required, "description": spec.description}
                for name, spec in self.params.items()
            },
            "returns": self.returns,
            "examples": list(self.examples),
        }


ACTION_REGISTRY: dict[str, ActionDefinition] = {
    "get_forward_msg": ActionDefinition(
        "get_forward_msg", "_get_forward_msg", "读取当前账号已解析的合并转发",
        {"id": ParamSpec(), "message_id": ParamSpec()}, {"type": "object"}, read_only=True,
    ),
    "send_private_msg": ActionDefinition(
        name="send_private_msg",
        handler="_send_private_msg",
        summary="发送私聊消息",
        params={
            "user_id": ParamSpec("int", True, "目标 OneBot user_id"),
            "message": ParamSpec(required=True, description="CQ 字符串、消息段数组或单个消息段对象"),
            "auto_escape": ParamSpec("bool"),
        },
        returns={"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]},
        examples=({
            "summary": "向好友发送文本",
            "params": {"user_id": 10001, "message": "你好"},
        },),
    ),
    "send_group_msg": ActionDefinition(
        name="send_group_msg",
        handler="_send_group_msg",
        summary="发送群消息，支持真实 @",
        params={
            "group_id": ParamSpec("int", True, "目标 OneBot group_id"),
            "message": ParamSpec(required=True, description="CQ 字符串、消息段数组或单个消息段对象"),
            "auto_escape": ParamSpec("bool"),
        },
        returns={"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]},
        examples=({
            "summary": "群内 @ 成员并发送文本",
            "params": {
                "group_id": 20001,
                "message": [
                    {"type": "at", "data": {"qq": 10001, "name": "Alice"}},
                    {"type": "text", "data": {"text": " 你好"}},
                ],
            },
        },),
    ),
    "send_msg": ActionDefinition(
        name="send_msg",
        handler="_send_msg",
        summary="根据 message_type 或目标字段发送消息",
        params={"message": ParamSpec(required=True, description="CQ 字符串、消息段数组或单个消息段对象"),
                "auto_escape": ParamSpec("bool")},
        returns={"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]},
        examples=({
            "summary": "按群号发送",
            "params": {"message_type": "group", "group_id": 20001, "message": "你好"},
        },),
    ),
    "get_login_info": ActionDefinition("get_login_info", "_get_login_info", "获取当前登录账号", {}, {"type": "object"}, read_only=True),
    "get_status": ActionDefinition("get_status", "_get_status", "获取运行状态", {}, {"type": "object"}, read_only=True),
    "get_version_info": ActionDefinition("get_version_info", "_get_version_info", "获取协议端版本", {}, {"type": "object"}, read_only=True),
    "get_friend_list": ActionDefinition("get_friend_list", "_get_friend_list", "获取好友列表", {}, {"type": "array"}, read_only=True),
    "get_stranger_info": ActionDefinition(
        "get_stranger_info", "_get_stranger_info", "获取已观测的用户资料",
        {"user_id": ParamSpec("int", True, "OneBot user_id"),
         "no_cache": ParamSpec("bool", False, "尝试刷新好友资料")},
        {"type": "object"}, read_only=True,
    ),
    "get_group_list": ActionDefinition("get_group_list", "_get_group_list", "获取已发现群列表", {}, {"type": "array"}, read_only=True),
    "get_group_info": ActionDefinition(
        "get_group_info", "_get_group_info", "获取群信息",
        {"group_id": ParamSpec("int", True, "OneBot group_id"), "no_cache": ParamSpec("bool")}, {"type": "object"}, read_only=True,
    ),
    "get_group_member_list": ActionDefinition(
        "get_group_member_list", "_get_group_member_list", "获取群成员列表",
        {"group_id": ParamSpec("int", True, "OneBot group_id")}, {"type": "array"}, read_only=True,
    ),
    "get_group_member_info": ActionDefinition(
        "get_group_member_info", "_get_group_member_info", "获取群成员信息",
        {"group_id": ParamSpec("int", True, "OneBot group_id"),
         "user_id": ParamSpec("int", True, "OneBot user_id"), "no_cache": ParamSpec("bool")},
        {"type": "object"}, read_only=True,
    ),
    "get_msg": ActionDefinition(
        "get_msg", "_get_msg", "通过 signed 32-bit message_id 获取持久化消息",
        {"message_id": ParamSpec("signed_int32", True, "非零 signed int32；负值合法")},
        {"type": "object"}, read_only=True,
    ),
    "get_group_msg_history": ActionDefinition(
        "get_group_msg_history", "_get_group_msg_history",
        "读取 Runtime 启动后已持久化的群消息",
        {
            "group_id": ParamSpec("int", True, "OneBot group_id"),
            "message_id": ParamSpec("signed_int32", False, "可选游标消息 ID"),
            "count": ParamSpec("int", False, "条数，默认 20，最大 100"),
            "reverse_order": ParamSpec("bool", False, "true 时读取游标之后的消息"),
        },
        {"type": "array"}, read_only=True,
    ),
    "get_friend_msg_history": ActionDefinition(
        "get_friend_msg_history", "_get_friend_msg_history",
        "读取 Runtime 启动后已持久化的好友消息",
        {
            "user_id": ParamSpec("int", True, "OneBot user_id"),
            "message_id": ParamSpec("signed_int32", False, "可选游标消息 ID"),
            "count": ParamSpec("int", False, "条数，默认 20，最大 100"),
            "reverse_order": ParamSpec("bool", False, "true 时读取游标之后的消息"),
        },
        {"type": "array"}, read_only=True,
    ),
    "can_send_image": ActionDefinition("can_send_image", "_can_send_image", "是否可发送图片", {}, {"type": "object"}, read_only=True),
    "can_send_record": ActionDefinition("can_send_record", "_can_send_record", "是否可发送语音", {}, {"type": "object"}, read_only=True),
    "get_capabilities": ActionDefinition("get_capabilities", "_get_capabilities", "获取驱动、动作与能力矩阵", {}, {"type": "object"}, read_only=True),
}


def action_catalog() -> list[dict[str, Any]]:
    return [ACTION_REGISTRY[name].public_metadata() for name in sorted(ACTION_REGISTRY)]
