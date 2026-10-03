"""Dashboard endpoints: read the apartment, and control it without the model.

These share the safety validator with the voice path. A button in the app is not a
privileged caller -- it gets clamped and refused on exactly the same rules.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import Container, CurrentPrincipal
from app.api.schemas import (
    BatchCommandRequest,
    CommandRequest,
    CommandResponse,
    DeviceListResponse,
    RoomResponse,
    SceneResponse,
)
from app.core.errors import NotFoundError
from app.domain.commands import Command
from app.domain.state import DeviceView

router = APIRouter(prefix="/api/v1", tags=["devices"])


@router.get("/rooms", response_model=list[RoomResponse], summary="List rooms")
async def list_rooms(container: Container, _: CurrentPrincipal) -> list[RoomResponse]:
    return [
        RoomResponse(
            id=room.id,
            name=room.name,
            device_ids=[d.id for d in container.home.devices_in_room(room.id)],
        )
        for room in container.home.rooms
    ]


@router.get("/devices", response_model=DeviceListResponse, summary="List devices with state")
async def list_devices(
    container: Container,
    _: CurrentPrincipal,
    room: str | None = Query(default=None, description="Filter by room id"),
) -> DeviceListResponse:
    if room and room not in container.home.room_map:
        raise NotFoundError(f"Unknown room '{room}'")
    views = await container.state.views(room=room)
    return DeviceListResponse(devices=views, count=len(views))


@router.get("/devices/{device_id}", response_model=DeviceView, summary="Read one device")
async def get_device(device_id: str, container: Container, _: CurrentPrincipal) -> DeviceView:
    device = container.home.device(device_id)  # raises NotFoundError
    views = await container.state.views(room=device.room)
    for view in views:
        if view.id == device_id:
            return view
    raise NotFoundError(f"Unknown device '{device_id}'")  # pragma: no cover


@router.post(
    "/devices/{device_id}/command",
    response_model=CommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Set one capability",
)
async def send_command(
    device_id: str,
    body: CommandRequest,
    container: Container,
    principal: CurrentPrincipal,
) -> CommandResponse:
    container.home.device(device_id)
    command = Command(
        device_id=device_id,
        capability=body.capability,
        value=body.value,
        delay_s=body.delay_s,
        reason=f"api:{principal.id}",
    )
    plan, report = await container.orchestrator.execute_commands(
        [command], session_id=f"api:{principal.id}"
    )
    return CommandResponse(
        plan=plan.as_dict(),
        dispatched=report.delivered_count if report else 0,
        failed=[{"device_id": r.command.device_id, "error": r.error} for r in (report.failed if report else [])],
    )


@router.post(
    "/commands",
    response_model=CommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Set several capabilities at once",
)
async def send_commands(
    body: BatchCommandRequest,
    container: Container,
    principal: CurrentPrincipal,
) -> CommandResponse:
    commands = [
        Command(
            device_id=item.device_id,
            capability=item.capability,
            value=item.value,
            delay_s=item.delay_s,
            reason=f"api:{principal.id}",
        )
        for item in body.commands
    ]
    plan, report = await container.orchestrator.execute_commands(
        commands, session_id=f"api:{principal.id}"
    )
    return CommandResponse(
        plan=plan.as_dict(),
        dispatched=report.delivered_count if report else 0,
        failed=[{"device_id": r.command.device_id, "error": r.error} for r in (report.failed if report else [])],
    )


@router.get("/scenes", response_model=list[SceneResponse], summary="List scenes")
async def list_scenes(container: Container, _: CurrentPrincipal) -> list[SceneResponse]:
    return [
        SceneResponse(
            id=scene.id,
            name=scene.name,
            description=scene.description,
            actions=[dict(a) for a in scene.actions],
        )
        for scene in container.home.scenes
    ]


@router.post(
    "/scenes/{scene_id}/activate",
    response_model=CommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Activate a scene",
)
async def activate_scene(
    scene_id: str,
    container: Container,
    principal: CurrentPrincipal,
) -> CommandResponse:
    commands = container.validator.expand_scene(scene_id)
    if not commands:
        raise NotFoundError(f"Unknown scene '{scene_id}'")
    plan, report = await container.orchestrator.execute_commands(
        commands, session_id=f"api:{principal.id}"
    )
    return CommandResponse(
        plan=plan.as_dict(),
        dispatched=report.delivered_count if report else 0,
        failed=[{"device_id": r.command.device_id, "error": r.error} for r in (report.failed if report else [])],
    )
