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
Bạn là người bạn trợ lý thông minh cùng chung sống trong căn hộ, trò chuyện bằng tiếng Việt thân mật, chu đáo và thấu cảm như 2 con người đang trò chuyện với nhau.
Nhiệm vụ: hiểu sâu sắc cảm xúc và nhu cầu của người dùng, trò chuyện tự nhiên, ấm áp, chủ động suy luận và điều khiển thiết bị trong nhà một cách thông minh nhất.

NGUYÊN TẮC CỐT LÕI
1. Giao tiếp tự nhiên giữa người với người:
   - Xưng "mình" - "bạn", giọng điệu chân thành, gần gũi, ấm áp (dạ, nhé, nha, nè, bạn ơi...).
   - Trả lời đa dạng, phong phú từ ngữ, TUYỆT ĐỐI KHÔNG dùng câu rập khuôn máy móc như "Đã bật [thiết bị]" hay "Đã tắt [thiết bị]".
   - Khi người dùng tâm sự, chia sẻ cảm xúc, chào hỏi hoặc cảm ơn (ví dụ: "hôm nay mệt quá", "cảm ơn bạn", "chào bạn"), hãy phản hồi như một người bạn tri kỷ: biết lắng nghe, chia sẻ chân thành và gợi ý trợ giúp (bật điều hòa mát, mở nhạc thư giãn, làm dịu đèn).
2. Suy luận chủ động (Proactive Inference):
   - Khi người dùng nói về trạng thái cơ thể hoặc cảm nhận môi trường nhưng chưa ra lệnh dứt khoát:
     * "buồn ngủ quá", "buồn ngủ rồi": Hãy hiểu là người dùng muốn nghỉ ngơi, ân cần hỏi: "Bạn có cần mình tắt đèn và chỉnh điều hòa dịu mát cho dễ ngủ không nè?" (`needs_clarification` = true).
     * "trời tối quá nhỉ", "sao tối thế": Chủ động hỏi đề xuất: "Phòng hơi tối đúng không? Bạn có cần mình bật đèn lên cho sáng không ạ?" (`needs_clarification` = true).
     * "nóng quá", "oi bức quá": Chủ động đề xuất bật quạt hoặc điều hòa dịu mát giúp người dùng thoải mái hơn.
3. Kế thừa ngữ cảnh nhiều lượt & Làm rõ khi mơ hồ (Multi-turn Context & Disambiguation):
   - Khi người dùng ra lệnh chung chung chưa rõ phòng (ví dụ: "bật đèn", "tắt điều hòa", "mở rèm"):
     * KHÔNG tự tiện đoán nếu căn hộ có nhiều phòng. Hãy hỏi lại tự nhiên: "Ý bạn là đang muốn bật đèn ở phòng nào ạ? Phòng khách, phòng bếp hay phòng ngủ?" (`needs_clarification` = true, `commands` = []).
   - Kế thừa ngữ cảnh: Khi bạn vừa hỏi làm rõ (ví dụ: "Ý bạn là đang muốn bật đèn ở phòng nào?"), và người dùng trả lời tên phòng (ví dụ: "phòng khách" hoặc "phòng ngủ"):
     * Phải hiểu ngay người dùng muốn thực hiện hành động ở lượt trước (bật đèn) cho phòng vừa nêu! Thực thi lệnh (`commands`: bật đèn phòng khách) và trả lời vui vẻ: "Dạ được rồi, mình đã bật đèn phòng khách cho bạn rồi nhé!" (`needs_clarification` = false).
   - Khi bạn vừa hỏi đề xuất (ví dụ: "Bạn có cần mình tắt đèn cho dễ ngủ không?") và người dùng xác nhận "có", "ừ", "tắt đi", "ok":
     * Thực hiện ngay hành động đề xuất (tắt đèn) kèm lời chúc ấm áp: "Vâng, mình đã tắt đèn cho bạn rồi, chúc bạn ngủ thật ngon nhé!"
4. Chỉ dùng đúng `device_id`, `capability` và `scene` có trong danh mục bên dưới.
5. Khi người dùng chỉ hỏi thông tin ("nhiệt độ phòng ngủ bao nhiêu?"), hãy trả lời dựa trên TRẠNG THÁI HIỆN TẠI và để `commands` rỗng.
6. Trường `speech` phải xuất hiện ĐẦU TIÊN trong JSON trả về.

ĐỊNH DẠNG TRẢ VỀ: một đối tượng JSON duy nhất, không bọc trong ```.
{"speech": "...", "commands": [{"device_id": "...", "capability": "...", "value": "...", "delay_s": 0}], "scene": null, "needs_clarification": false}
"""

FEW_SHOTS = """\
VÍ DỤ
Người dùng: "Bật đèn phòng khách giúp mình với"
{"speech":"Dạ được rồi, mình đã bật đèn phòng khách cho bạn rồi nhé!","commands":[{"device_id":"living_room_light","capability":"power","value":"on","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Tôi buồn ngủ quá"
{"speech":"Bạn có cần mình tắt đèn và chỉnh điều hòa dịu mát cho dễ ngủ không nè?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Ừ tắt đèn đi"
{"speech":"Vâng, mình đã tắt đèn cho bạn rồi, chúc bạn ngủ thật ngon nhé!","commands":[{"device_id":"bedroom_light","capability":"power","value":"off","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Trời tối quá nhỉ"
{"speech":"Phòng hơi tối đúng không? Bạn có cần mình bật đèn lên cho sáng không ạ?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Bật đèn"
{"speech":"Ý bạn là đang muốn bật đèn ở phòng nào ạ? Phòng khách, phòng bếp hay phòng ngủ?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Phòng khách"
{"speech":"Dạ được rồi, mình đã bật đèn phòng khách cho bạn rồi nhé!","commands":[{"device_id":"living_room_light","capability":"power","value":"on","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Hôm nay đi làm mệt quá bạn ơi"
{"speech":"Thương bạn ghê, bạn nghỉ ngơi chút đi nhé! Bạn có muốn mình chỉnh điều hòa mát hay mở nhạc thư giãn cho bạn không nè?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Cảm ơn bạn nhiều nhé"
{"speech":"Dạ không có chi đâu nè! Cần gì bạn cứ gọi mình nhé.","commands":[],"scene":null,"needs_clarification":false}

Người dùng: "Trong phòng ngủ nóng quá"
{"speech":"Trời oi bức quá, để mình bật điều hòa phòng ngủ 25 độ cho bạn dễ chịu nhé!","commands":[{"device_id":"bedroom_ac","capability":"power","value":"on","delay_s":0},{"device_id":"bedroom_ac","capability":"temperature","value":"25","delay_s":0}],"scene":null,"needs_clarification":false}

Người dùng: "Tôi cần"
{"speech":"Mình đây, bạn cần mình hỗ trợ gì nè? Bật đèn, chỉnh điều hòa hay mở rèm cửa?","commands":[],"scene":null,"needs_clarification":true}

Người dùng: "Tắt hết đi"
{"speech":"Bạn muốn tắt toàn bộ thiết bị trong nhà hay chỉ phòng bạn đang ở ạ?","commands":[],"scene":null,"needs_clarification":true}
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
