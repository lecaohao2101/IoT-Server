"""Nói một thao tác thiết bị bằng tiếng Việt.

Dùng chung giữa lớp an toàn (dựng câu hỏi xác nhận) và bộ suy luận offline (dựng
lời đề xuất). Để mỗi bên tự viết lấy là cách chắc chắn để cùng một việc được đọc
thành hai câu khác nhau -- người dùng nghe đề xuất một kiểu rồi nghe hỏi lại một
kiểu khác, và không chắc hai câu đó có nói về cùng một thứ không.

Ở đây chỉ có cách diễn đạt. Không luật, không quyết định -- những thứ đó thuộc về
`app/safety/`.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.utils import fold


class _HasName(Protocol):
    name: str


class _HasCapability(Protocol):
    capability: str
    value: Any


#: Cách đọc từng thuộc tính thành tiếng Việt. Thiếu bản dịch thì câu hỏi xác nhận
#: hoá ra "đổi 'brightness' thành 70" -- không ai nói chuyện kiểu đó.
_CAPABILITY_PHRASES: dict[str, str] = {
    "brightness": "chỉnh độ sáng {name} lên {value}%",
    "temperature": "đặt {name} ở {value} độ",
    "volume": "chỉnh âm lượng {name} còn {value}%",
    "speed": "chỉnh tốc độ {name} mức {value}",
    "mode": "chuyển {name} sang chế độ {value}",
    "color_temp": "đổi màu ánh sáng {name} thành {value}",
    "position": "kéo {name} tới vị trí {value}%",
}


def describe_command(device: _HasName, capability: str, value: Any) -> str:
    """Một lệnh, đọc lên như người nói: "bật Đèn phòng khách"."""
    name = device.name
    if capability == "power":
        return f"{'bật' if _truthy(value) else 'tắt'} {name}"
    if capability == "locked":
        return f"{'khoá' if _truthy(value) else 'mở khoá'} {name}"
    if capability == "state":
        text = str(value)
        if text in {"open", "mở"}:
            return f"mở {name}"
        if text in {"close", "closed", "đóng"}:
            return f"đóng {name}"
    phrase = _CAPABILITY_PHRASES.get(capability)
    if phrase:
        return phrase.format(name=name, value=speak_value(value))
    return f"đổi {capability} của {name} thành {speak_value(value)}"


#: Dạng NGẮN, không kèm tên thiết bị: dùng cho lệnh thứ hai trở đi của cùng một
#: thiết bị, khi tên đã được nhắc ở vế đầu.
_CAPABILITY_SHORT: dict[str, str] = {
    "brightness": "độ sáng {value}%",
    "temperature": "{value} độ",
    "volume": "âm lượng {value}%",
    "speed": "tốc độ mức {value}",
    "mode": "chế độ {value}",
    "color_temp": "ánh sáng {value}",
    "position": "vị trí {value}%",
}

#: Những thuộc tính đáng đứng đầu một vế, vì chúng là hành động chính.
_PRIMARY_CAPABILITIES = ("power", "locked", "state")

#: Liệt kê quá chừng này cái tên thì câu hỏi dài hơn cả việc tự đi bật công tắc.
_MAX_NAMED = 4


def describe_device_actions(device: _HasName, commands: list[_HasCapability]) -> str:
    """Mọi thay đổi trên MỘT thiết bị, gộp thành một vế, tên nhắc đúng một lần."""
    head_command = next(
        (c for c in commands if c.capability in _PRIMARY_CAPABILITIES), commands[0]
    )
    head = describe_command(device, head_command.capability, head_command.value)

    tails: list[str] = []
    for command in commands:
        if command is head_command:
            continue
        short = _CAPABILITY_SHORT.get(command.capability)
        tails.append(
            short.format(value=speak_value(command.value))
            if short
            else f"{command.capability} {speak_value(command.value)}"
        )
    return ", ".join([head, *tails])


def join_actions(parts: list[str]) -> str:
    """Nối các việc thành một vế đọc lên được.

    Hai thứ phải xử lý, cả hai đều phát hiện khi nghe thật: "tắt hết đèn" cho ra
    bảy việc, và câu "tắt Đèn phòng khách, tắt Đèn bếp, tắt Đèn phòng ngủ, ..."
    lặp chữ "tắt" bảy lần nghe như máy đọc. Nên động từ chung được rút ra ngoài,
    và danh sách dài thì cắt bớt.
    """
    if len(parts) == 1:
        return parts[0]

    # Cùng một động từ cho mọi việc ("tắt X", "tắt Y") -> "tắt X, Y và Z".
    #
    # Chỉ làm khi mỗi vế là một cụm đơn. Vế đã có dấu phẩy nghĩa là thiết bị đó
    # mang nhiều thay đổi, và rút động từ ra ngoài lúc ấy sẽ dính hai thiết bị
    # vào nhau: "bật Đèn A, độ sáng 90%, Đèn B" đọc như thể độ sáng là của B.
    verbs = {p.split(" ", 1)[0] for p in parts}
    if len(verbs) == 1 and all(" " in p and "," not in p for p in parts):
        verb = parts[0].split(" ", 1)[0]
        names = [p.split(" ", 1)[1] for p in parts]
        return f"{verb} {_join_names(names)}"
    return _join_names(parts)


def _join_names(names: list[str]) -> str:
    if len(names) > _MAX_NAMED:
        shown = ", ".join(names[:_MAX_NAMED])
        return f"{shown} và {len(names) - _MAX_NAMED} thiết bị nữa"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " và " + names[-1]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return fold(str(value)) in {"on", "true", "1", "bat", "bật", "open", "mở"}


def speak_value(value: Any) -> str:
    """Render a value for a spoken prompt -- never leak Python's `True`/`False`."""
    if isinstance(value, bool):
        return "bật" if value else "tắt"
    return str(value)
