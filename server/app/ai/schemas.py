"""The contract the LLM must answer with.

Every value is a string. That is deliberate: providers differ in how faithfully
they honour union types in a response schema, and the capability layer already
knows how to turn ``"70"``, ``"bật"`` or ``"26.5"`` into the right typed value for
the device in question. One lenient boundary beats three strict ones that disagree.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.commands import Command


class ProposedCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(description="Mã thiết bị chính xác, lấy từ danh mục thiết bị.")
    capability: str = Field(description="Tên thuộc tính chính xác của thiết bị đó.")
    value: str = Field(description="Giá trị mong muốn, dạng chuỗi. Ví dụ: 'on', '70', '26'.")
    delay_s: float = Field(default=0.0, ge=0.0, le=3600.0, description="Hẹn giờ, tính bằng giây.")

    def to_command(self, reason: str | None = None) -> Command:
        return Command(
            device_id=self.device_id.strip(),
            capability=self.capability.strip(),
            value=self.value,
            delay_s=self.delay_s,
            reason=reason,
        )


class AssistantReply(BaseModel):
    """Structured answer: what to say, and what to do."""

    model_config = ConfigDict(extra="ignore")

    speech: str = Field(
        description=(
            "Câu trả lời bằng tiếng Việt, ngắn gọn, tự nhiên, tối đa 2 câu. "
            "Không đọc mã thiết bị, không dùng markdown."
        )
    )
    commands: list[ProposedCommand] = Field(
        default_factory=list, description="Danh sách lệnh cần thực hiện. Để trống nếu chỉ trò chuyện."
    )
    scene: str | None = Field(
        default=None, description="Mã ngữ cảnh (scene) cần kích hoạt, nếu người dùng yêu cầu."
    )
    needs_clarification: bool = Field(
        default=False, description="True khi yêu cầu còn mơ hồ và cần hỏi lại người dùng."
    )

    def to_commands(self) -> list[Command]:
        return [c.to_command(reason="llm") for c in self.commands]


def response_json_schema() -> dict[str, Any]:
    """JSON Schema handed to the provider for constrained decoding."""
    schema = AssistantReply.model_json_schema()
    schema["title"] = "AssistantReply"
    return schema


#: Field name the streaming extractor watches so TTS can start before the JSON ends.
SPEECH_FIELD = "speech"
