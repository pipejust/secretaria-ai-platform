"""Acten bot ingress v1. Portable contract; no provider or database dependencies."""

import hashlib
import hmac
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSegment(ContractModel):
    id: str = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=10000)
    speaker_id: str | None
    speaker_name: str | None
    identity_source: str = "unknown"
    person_external_id: str | None = None
    potential_speaker_names: list[dict] = Field(default_factory=list)
    tokens: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_order(self):
        if self.end < self.start:
            raise ValueError("Invalid segment bounds")
        return self


class SourceClaim(ContractModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class SourceChapter(ContractModel):
    title: str = Field(min_length=1)
    description: str
    evidence_ids: list[str] = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)


class Recording(BaseModel):
    """Grabación entregada por el proveedor de captura.

    Compatible con el diccionario libre anterior: todo es opcional y se
    admiten claves extra. `kind` distingue el WebM solo audio del que trae
    imagen; `expires_at` es la fecha en que el proveedor borra el archivo.
    """

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    kind: Literal["audio", "video"] = "audio"
    format: str | None = None
    expires_at: str | None = None
    available_until: str | None = None
    local_audio_path: str | None = None


class CaptureData(ContractModel):
    external_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    date: AwareDatetime
    date_source: Literal["scheduled_start", "bot_requested_at"]
    language_hint: str
    timebase: Literal["seconds_from_recording_start"]
    transcript: list[SourceSegment] = Field(min_length=1, max_length=100000)
    summary: list[SourceClaim] = Field(min_length=1, max_length=6)
    key_points: list[SourceClaim]
    timeline: list[SourceChapter]
    participants: list[Any]
    recording: Recording = Field(default_factory=Recording)
    warnings: list[str]
    provenance: dict[str, Any]
    key_moments: list[dict] = Field(default_factory=list)
    speaker_timeline: list[dict] = Field(default_factory=list)
    speakers: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_sources(self):
        by_id = {s.id: s for s in self.transcript}
        if len(by_id) != len(self.transcript):
            raise ValueError("Duplicate segment IDs")
        order = [(s.start, s.end, s.id) for s in self.transcript]
        if order != sorted(order):
            raise ValueError("Transcript must be ordered")
        for claim in [*self.summary, *self.key_points, *self.timeline]:
            if not set(claim.evidence_ids).issubset(by_id):
                raise ValueError("Missing evidence")
        for chapter in self.timeline:
            evidence = [by_id[sid] for sid in chapter.evidence_ids]
            if chapter.start != min(s.start for s in evidence) or chapter.end != max(
                s.end for s in evidence
            ):
                raise ValueError("Timeline bounds do not match source")
        return self

    def transcript_text(self) -> str:
        # Preserve unlabelled speakers as unknown; never infer emails/employee IDs.
        lines = []
        for segment in self.transcript:
            speaker = segment.speaker_name
            if not speaker:
                speaker = (
                    "Hablante " + segment.speaker_id
                    if segment.speaker_id is not None
                    else "Hablante sin identificar"
                )
            lines.append(f"[{speaker}] {segment.text}")
        return "\n".join(lines)

    def summary_text(self) -> str:
        # Headings follow Acten's supported output languages. Evidence stays structured.
        headings = {
            "es": ("Resumen", "Puntos clave"),
            "en": ("Summary", "Key points"),
            "ca": ("Resum", "Punts clau"),
        }
        overview, points = headings.get(self.language_hint.split("-")[0], headings["es"])
        text = f"### {overview}\n" + "\n".join(f"- {c.text}" for c in self.summary)
        if self.key_points:
            text += f"\n\n### {points}\n" + "\n".join(f"- {c.text}" for c in self.key_points)
        return text


class ActenBotEvent(ContractModel):
    schema_version: Literal["acten.bot.v1"] = "acten.bot.v1"
    id: str
    type: Literal["meeting.completed"] = "meeting.completed"
    meeting_id: UUID
    tenant_id: int = Field(gt=0)
    occurred_at: AwareDatetime
    data: CaptureData

    @model_validator(mode="after")
    def event_identity(self):
        if self.id != f"meeting.completed:{self.meeting_id}:v1":
            raise ValueError("Event ID does not match meeting")
        return self


def event_signature(secret: str, timestamp: str, body: bytes) -> str:
    return (
        "v1="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


def verify_signature(secret: str, timestamp: str, signature: str, body: bytes, now: float):
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        raise ValueError("Invalid timestamp")
    if abs(now - int(timestamp)) > 300:
        raise ValueError("Expired signature")
    if not hmac.compare_digest(
        event_signature(secret, timestamp, body).encode(), signature.encode()
    ):
        raise ValueError("Invalid signature")
