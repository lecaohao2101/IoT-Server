"""Prompt construction for the reasoning stage.

Three principles hold this together:

* **The catalogue is generated, never hand-written.** The model can only name ids
  that exist because the ids come from ``home.yaml`` on every turn.
* **The prompt asks for safety, the validator enforces it.** Instructions here
  reduce the number of rejected commands; they are not what keeps the door locked.
* **`speech` comes first in the JSON.** The orchestrator streams that field to TTS
  while the rest of the object is still being generated, so the reply starts
  playing roughly a second earlier.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.home import Capability, CapabilityKind, HomeConfig

_WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

SYSTEM_HEADER = """\
Bạn là trợ lý giọng nói của một căn hộ thông minh, nói chuyện bằng tiếng Việt như một người bạn thân thiện, chu đáo.
Nhiệm vụ: hiểu sâu sắc mong muốn của người dùng, trò chuyện tự nhiên, ấm áp và điều khiển thiết bị phù hợp.

NGUYÊN TẮC
1. Giao tiếp tự nhiên giữa người với người: Xưng "mình" - "bạn". Trả lời ấm áp, ngắn gọn (tối đa 2 câu),
   không đọc mã thiết bị, không nói kiểu robot máy móc.
2. Thấu hiểu nhu cầu và ngữ cảnh sinh hoạt: Khi người dùng nói về hoạt động hoặc cảm giác
   (ví dụ: "muốn đọc sách", "tối quá", "nóng quá", "đi ngủ"), hãy suy luận để kích hoạt ngữ cảnh (scene)
   hoặc điều chỉnh thiết bị thích hợp (đọc sách -> bật đèn 80% ánh sáng ấm; nóng quá -> bật điều hòa 25 độ).
3. Xử lý câu lửng lơ hoặc mơ hồ: Nếu người dùng nói chưa hết câu (ví dụ: "Tôi cần...", "Giúp tôi với"),
   đừng từ chối máy móc. Hãy hỏi lại gợi ý một cách thân thiện (`needs_clarification` = true):
   "Bạn cần mình hỗ trợ gì ạ? Bật đèn, chỉnh điều hòa hay mở rèm?"
4. Chỉ dùng đúng `device_id`, `capability` và `scene` có trong danh mục bên dưới.
5. Khi người dùng chỉ hỏi thông tin ("nhiệt độ phòng ngủ bao nhiêu?"), hãy trả lời
   dựa trên TRẠNG THÁI HIỆN TẠI và để `commands` rỗng.
6. Trường `speech` phải xuất hiện ĐẦU TIÊN trong JSON trả về.

ĐỊNH DẠNG TRẢ VỀ: một đối tượng JSON duy nhất, không bọc trong ```.
{"speech": "...", "commands": [{"device_id": "...", "capability": "...", "value": "...", "delay_s": 0}], "scene": null, "needs_clarification": false}
"""

FEW_SHOTS = """\
VÍ DỤ
Người dùng: "Bật đèn phòng khách lên 70%"
{"speech":"Đã bật đèn phòng khách ở mức 70%.","commands":[{"device_id":"living_room_light","capability":"power","value":"on","delay_s":0},{"device_id":"living_room_light","capability":"brightness","value":"70","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Bây giờ tôi muốn đọc sách"
{"speech":"Mình đã bật chế độ đọc sách cho bạn rồi nhé.","commands":[],"scene":"reading","needs_clarification":false}

Người dùng: "Tôi cần"
{"speech":"Mình đây, bạn cần mình hỗ trợ gì ạ? Bật đèn, chỉnh điều hòa hay mở rèm?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Trong phòng ngủ nóng quá"
{"speech":"Mình bật điều hòa phòng ngủ ở 25 độ nhé.","commands":[{"device_id":"bedroom_ac","capability":"power","value":"on","delay_s":0},{"device_id":"bedroom_ac","capability":"temperature","value":"25","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Phòng tối quá"
{"speech":"Để mình bật đèn sáng lên cho bạn nhé.","commands":[{"device_id":"living_room_light","capability":"power","value":"on","delay_s":0},{"device_id":"living_room_light","capability":"brightness","value":"100","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Nhiệt độ ngoài ban công bao nhiêu?"
{"speech":"Hiện ban công khoảng 31 độ.","commands":[],"scene":null,"needs_clarification":false}

Người dùng: "Tắt hết đi"
{"speech":"Bạn muốn tắt đèn ở tất cả các phòng, hay chỉ phòng bạn đang ở?","commands":[],"scene":null,"needs_clarification":true}
"""


def _describe_capability(cap: Capability) -> str:
    if cap.kind is CapabilityKind.ENUM:
        domain = "|".join(cap.values)
    elif cap.kind is CapabilityKind.NUMBER:
        unit = f" {cap.unit}" if cap.unit else ""
        step = f", bước {cap.step:g}" if cap.step else ""
        domain = f"{cap.minimum:g}..{cap.maximum:g}{unit}{step}"
    elif cap.kind is CapabilityKind.BOOLEAN:
        domain = "true|false"
    else:
        domain = "chuỗi"

    flags = []
    if cap.read_only:
        flags.append("chỉ đọc")
    if cap.sensitive:
        flags.append("cần xác nhận")
    suffix = f" [{', '.join(flags)}]" if flags else ""
    return f"{cap.name}={domain}{suffix}"


def render_catalogue(home: HomeConfig) -> str:
    """Devices grouped by room, one line each, in the model's working vocabulary."""
    rooms = {room.id: room for room in home.rooms}
    lines: list[str] = []
    for room_id, room in rooms.items():
        devices = home.devices_in_room(room_id)
        if not devices:
            continue
        lines.append(f"# {room.name} ({room_id})")
        for device in devices:
            caps = "; ".join(_describe_capability(c) for c in device.capabilities.values())
            lines.append(f"- {device.id} | {device.name} | {device.type.value} | {caps}")
    return "\n".join(lines)


def render_scenes(home: HomeConfig) -> str:
    if not home.scenes:
        return ""
    lines = ["NGỮ CẢNH CÓ SẴN (đặt vào trường `scene`, không cần liệt kê lệnh):"]
    for scene in home.scenes:
        description = f" — {scene.description}" if scene.description else ""
        aliases = f" (còn gọi: {', '.join(scene.aliases)})" if scene.aliases else ""
        lines.append(f"- {scene.id} | {scene.name}{aliases}{description}")
    return "\n".join(lines)


def format_now(moment: datetime) -> str:
    weekday = _WEEKDAYS_VI[moment.weekday()]
    return f"{weekday}, {moment.strftime('%d/%m/%Y %H:%M')}"


def build_system_prompt(
    home: HomeConfig,
    *,
    state_text: str,
    now: datetime,
    current_room: str | None = None,
    quiet_hours: bool = False,
    policy_notes: list[str] | None = None,
) -> str:
    rooms = home.room_map
    room_line = "không xác định"
    if current_room and current_room in rooms:
        room_line = f"{rooms[current_room].name} ({current_room})"
    elif current_room:
        room_line = current_room

    sections = [
        SYSTEM_HEADER,
        f"BỐI CẢNH\n- Căn hộ: {home.name}\n- Thời gian: {format_now(now)}\n- Phòng hiện tại: {room_line}",
    ]
    if quiet_hours:
        sections.append(
            "- Đang trong khung giờ yên tĩnh: ưu tiên âm lượng và độ sáng thấp, "
            "tránh bật thiết bị gây ồn."
        )
    if policy_notes:
        sections.append("GIỚI HẠN AN TOÀN\n" + "\n".join(f"- {n}" for n in policy_notes))

    sections.append("DANH MỤC THIẾT BỊ\n" + render_catalogue(home))
    scenes = render_scenes(home)
    if scenes:
        sections.append(scenes)
    sections.append("TRẠNG THÁI HIỆN TẠI\n" + state_text)
    sections.append(FEW_SHOTS)
    return "\n\n".join(sections)


def build_policy_notes(home: HomeConfig, policy) -> list[str]:  # noqa: ANN001 - avoids import cycle
    """Turn the machine-readable policy into hints the model can respect up front."""
    notes: list[str] = [f"Mỗi lượt tối đa {policy.max_commands_per_plan} lệnh."]
    for rule in policy.limits:
        target = rule.match.device_id or rule.match.device_type or rule.match.room or "mọi thiết bị"
        cap = rule.match.capability or "giá trị"
        bounds = []
        if rule.minimum is not None:
            bounds.append(f"tối thiểu {rule.minimum:g}")
        if rule.maximum is not None:
            bounds.append(f"tối đa {rule.maximum:g}")
        when = " (chỉ trong giờ yên tĩnh)" if rule.quiet_hours_only else ""
        notes.append(f"{target}: {cap} {' và '.join(bounds)}{when}.")
    for rule in policy.forbid:
        notes.append(rule.message)
    for device in home.devices:
        sensitive = [c.name for c in device.capabilities.values() if c.sensitive]
        if sensitive:
            notes.append(
                f"{device.name}: thao tác {', '.join(sensitive)} cần người dùng xác nhận trước."
            )
    return notes
