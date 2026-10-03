"""The safety layer is the part that must not be wrong. It gets the most tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.commands import Command, RejectionCode
from app.domain.home import load_home_config
from app.domain.state import DeviceState, HomeSnapshot
from app.safety.rules import (
    ConfirmRule,
    ForbidRule,
    LimitRule,
    QuietHours,
    RateLimits,
    SafetyPolicy,
    Selector,
    load_safety_policy,
)
from app.safety.validator import CommandValidator


@pytest.fixture
def validator(home, policy) -> CommandValidator:
    return CommandValidator(home, policy)


DAYTIME = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
NIGHT = datetime(2026, 3, 2, 23, 30, tzinfo=UTC)


def one(validator, device_id, capability, value, **kw):
    return validator.validate([Command(device_id=device_id, capability=capability, value=value)], **kw)


# ------------------------------------------------------------------ happy path


def test_accepts_a_well_formed_command(validator):
    plan = one(validator, "living_room_light", "power", "on", now=DAYTIME)
    assert len(plan.accepted) == 1
    assert plan.accepted[0].value == "on"
    assert not plan.rejected


def test_normalises_vietnamese_and_numeric_values(validator):
    plan = validator.validate(
        [
            Command(device_id="kitchen_outlet", capability="power", value="bật"),
            Command(device_id="living_room_light", capability="brightness", value="70%"),
        ],
        now=DAYTIME,
    )
    assert [c.value for c in plan.accepted] == [True, 70]


def test_enum_matching_ignores_diacritics_and_case(validator):
    plan = one(validator, "living_room_ac", "mode", "COOL", now=DAYTIME)
    assert plan.accepted[0].value == "cool"


# --------------------------------------------------------------- bad references


def test_rejects_unknown_device(validator):
    plan = one(validator, "garage_light", "power", "on", now=DAYTIME)
    assert plan.rejected[0].code is RejectionCode.UNKNOWN_DEVICE
    assert not plan.accepted


def test_repairs_a_device_named_by_its_display_name(validator):
    plan = one(validator, "Đèn phòng khách", "power", "on", now=DAYTIME)
    assert plan.accepted[0].device_id == "living_room_light"


def test_rejects_unknown_capability(validator):
    plan = one(validator, "living_room_light", "turbo", "on", now=DAYTIME)
    assert plan.rejected[0].code is RejectionCode.UNKNOWN_CAPABILITY


def test_rejects_write_to_a_sensor(validator):
    plan = one(validator, "balcony_sensor", "temperature", "20", now=DAYTIME)
    assert plan.rejected[0].code is RejectionCode.READ_ONLY


def test_rejects_value_outside_an_enum(validator):
    plan = one(validator, "living_room_ac", "mode", "turbo", now=DAYTIME)
    assert plan.rejected[0].code is RejectionCode.INVALID_VALUE


def test_rejects_non_numeric_value_for_a_number(validator):
    plan = one(validator, "living_room_light", "brightness", "rất sáng", now=DAYTIME)
    assert plan.rejected[0].code is RejectionCode.INVALID_VALUE


# -------------------------------------------------------------------- clamping


def test_clamps_to_the_capability_range_and_says_so(validator):
    plan = one(validator, "living_room_light", "brightness", "350", now=DAYTIME)
    accepted = plan.accepted[0]
    assert accepted.value == 100
    assert accepted.was_adjusted


def test_policy_limit_is_tighter_than_the_device_range(validator):
    # The AC itself allows 16 degrees; safety.yaml floors it at 18.
    plan = one(validator, "bedroom_ac", "temperature", "16", now=DAYTIME)
    assert plan.accepted[0].value == 18
    assert plan.accepted[0].notes


def test_quiet_hours_limit_only_applies_at_night(home, policy):
    validator = CommandValidator(home, policy)
    day = one(validator, "bedroom_light", "brightness", "100", now=DAYTIME)
    assert day.accepted[0].value == 100

    validator = CommandValidator(home, policy)
    night = one(validator, "bedroom_light", "brightness", "100", now=NIGHT)
    assert night.accepted[0].value == 40


# ------------------------------------------------------------------ forbidding


def test_forbidden_at_night_allowed_by_day(home, policy):
    validator = CommandValidator(home, policy)
    night = one(validator, "bathroom_water_heater", "power", "on", now=NIGHT)
    assert night.rejected[0].code is RejectionCode.QUIET_HOURS
    assert not night.accepted

    validator = CommandValidator(home, policy)
    day = one(validator, "bathroom_water_heater", "power", "on", now=DAYTIME)
    assert day.accepted


def test_sensitive_capability_is_parked_for_confirmation(validator):
    plan = one(validator, "front_door_lock", "locked", "false", now=DAYTIME)
    assert not plan.accepted
    assert len(plan.pending_confirmation) == 1
    assert plan.pending_confirmation[0].value is False


def test_confirmation_can_be_skipped_once_the_user_said_yes(validator):
    plan = validator.validate(
        [Command(device_id="front_door_lock", capability="locked", value="false")],
        now=DAYTIME,
        skip_confirmation=True,
    )
    assert plan.accepted and not plan.pending_confirmation


# ------------------------------------------------------------------ plan shape


def test_plan_is_truncated_at_the_configured_maximum(home, policy):
    validator = CommandValidator(home, policy)
    commands = [
        Command(device_id=d.id, capability="power", value="off")
        for d in home.devices
        if "power" in d.writable_capabilities
    ][:12]
    plan = validator.validate(commands, now=DAYTIME)
    assert len(plan.accepted) <= policy.max_commands_per_plan
    assert any(r.code is RejectionCode.PLAN_TOO_LARGE for r in plan.rejected)


def test_duplicate_commands_keep_the_last_value(validator):
    plan = validator.validate(
        [
            Command(device_id="living_room_light", capability="brightness", value="30"),
            Command(device_id="living_room_light", capability="brightness", value="60"),
        ],
        now=DAYTIME,
    )
    assert len(plan.accepted) == 1
    assert plan.accepted[0].value == 60
    assert plan.rejected[0].code is RejectionCode.DUPLICATE


def test_per_device_rate_limit_eventually_refuses(validator):
    results = [
        one(validator, "living_room_light", "power", "on", now=DAYTIME) for _ in range(12)
    ]
    assert any(
        r.code is RejectionCode.RATE_LIMITED for plan in results for r in plan.rejected
    )


def test_session_rate_limit_rejects_the_whole_plan(home):
    policy = SafetyPolicy(
        rate_limits=RateLimits(plans_per_session_per_minute=1, session_burst=1)
    )
    validator = CommandValidator(home, policy)
    assert one(validator, "living_room_light", "power", "on", session_id="s").accepted
    blocked = one(validator, "living_room_light", "power", "off", session_id="s")
    assert blocked.rejected[0].code is RejectionCode.RATE_LIMITED


def test_offline_devices_are_refused_when_the_policy_demands_it(home):
    policy = SafetyPolicy(require_device_online=True)
    validator = CommandValidator(home, policy)
    snapshot = HomeSnapshot(
        devices={"living_room_light": DeviceState(device_id="living_room_light", online=False)}
    )
    plan = one(validator, "living_room_light", "power", "on", snapshot=snapshot)
    assert plan.rejected[0].code is RejectionCode.DEVICE_OFFLINE


# ----------------------------------------------------------------------- scenes


def test_scene_expands_into_concrete_commands(validator):
    commands = validator.expand_scene("good_night")
    assert commands
    assert all(c.reason == "scene:good_night" for c in commands)
    plan = validator.validate(commands, now=DAYTIME)
    assert plan.accepted


def test_scene_can_be_named_by_alias(validator):
    assert validator.expand_scene("đi ngủ")


def test_unknown_scene_expands_to_nothing(validator):
    assert validator.expand_scene("teleport") == []


# ---------------------------------------------------------------------- policy


def test_quiet_hours_window_wraps_midnight():
    window = QuietHours(enabled=True, start="22:00", end="06:00")
    assert window.contains(datetime(2026, 1, 1, 23, 0))
    assert window.contains(datetime(2026, 1, 1, 2, 0))
    assert not window.contains(datetime(2026, 1, 1, 12, 0))


def test_quiet_hours_accepts_pyyaml_sexagesimal_ints():
    # PyYAML turns an unquoted 22:00 into 1320.
    assert QuietHours(enabled=True, start=1320, end=360).contains(datetime(2026, 1, 1, 23, 0))


def test_selector_requires_at_least_one_constraint():
    with pytest.raises(ValueError):
        Selector()


def test_limit_rule_requires_a_bound():
    with pytest.raises(ValueError):
        LimitRule(match=Selector(capability="brightness"))


def test_policy_file_loads_and_has_teeth(settings):
    loaded = load_safety_policy(settings.resolve(settings.safety_config_path))
    assert loaded.quiet_hours.enabled
    assert loaded.limits and loaded.forbid


def test_missing_policy_file_falls_back_to_defaults(tmp_path):
    loaded = load_safety_policy(tmp_path / "nope.yaml")
    assert loaded.max_commands_per_plan == 8


def test_rules_match_by_room_and_type(home):
    device = home.device("bedroom_light")
    assert Selector(room="bedroom", capability="brightness").matches(device, "brightness")
    assert not Selector(room="kitchen").matches(device, "brightness")
    assert Selector(device_type="light").matches(device, "power")


def test_forbid_rule_without_a_value_blocks_every_value(home):
    policy = SafetyPolicy(
        forbid=(
            ForbidRule(match=Selector(device_id="living_room_tv", capability="power"), message="no"),
        )
    )
    validator = CommandValidator(home, policy)
    assert one(validator, "living_room_tv", "power", "off").rejected


def test_confirm_rule_matches_only_the_named_value(home):
    policy = SafetyPolicy(
        confirm=(
            ConfirmRule(
                match=Selector(device_id="living_room_tv", capability="power"),
                value="on",
                prompt="Bật tivi nhé?",
            ),
        )
    )
    validator = CommandValidator(home, policy)
    assert one(validator, "living_room_tv", "power", "on").pending_confirmation
    assert one(validator, "living_room_tv", "power", "off").accepted


# ------------------------------------------------------------------- home config


def test_home_config_assigns_default_mqtt_topics(home):
    device = home.device("living_room_light")
    assert device.mqtt.command_topic == "home/living_room/living_room_light/set"
    assert device.mqtt.state_topic == "home/living_room/living_room_light/state"


def test_home_config_rejects_a_device_in_an_unknown_room(tmp_path):
    from app.core.errors import ConfigError

    broken = tmp_path / "home.yaml"
    broken.write_text(
        "rooms: [{id: a, name: A}]\n"
        "devices: [{id: d, name: D, room: nowhere, capabilities: {power: {kind: boolean}}}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_home_config(broken)


def test_home_config_rejects_duplicate_device_ids(tmp_path):
    from app.core.errors import ConfigError

    broken = tmp_path / "home.yaml"
    broken.write_text(
        "rooms: [{id: a, name: A}]\n"
        "devices:\n"
        "  - {id: d, name: D, room: a, capabilities: {power: {kind: boolean}}}\n"
        "  - {id: d, name: E, room: a, capabilities: {power: {kind: boolean}}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_home_config(broken)


def test_capability_number_requires_bounds(tmp_path):
    from app.core.errors import ConfigError

    broken = tmp_path / "home.yaml"
    broken.write_text(
        "rooms: [{id: a, name: A}]\n"
        "devices: [{id: d, name: D, room: a, capabilities: {level: {kind: number}}}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_home_config(broken)


def test_step_snaps_values_to_the_grid(home):
    curtain = home.device("living_room_curtain")
    value, _ = curtain.capability("position").coerce("43")
    assert value == 45  # step is 5
