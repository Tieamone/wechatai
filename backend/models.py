from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Source(StrictModel):
    adapter: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=300)
    partition: str = Field(min_length=1, max_length=300)


class Message(StrictModel):
    schema_version: Literal[1] = 1
    conversation_id: str = Field(min_length=1, max_length=200)
    source: Source
    sender_role: Literal['self', 'peer', 'system']
    event_time: str | None = None
    observed_at: str | None = None
    type: Literal['text', 'image', 'voice', 'video', 'system', 'unsupported'] = 'text'
    text: str = Field(default='', max_length=20000)
    revision: int = Field(default=1, ge=1)

    @field_validator('event_time', 'observed_at')
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value

    def utc_time(self):
        if not self.event_time:
            return None
        date = datetime.fromisoformat(self.event_time.replace('Z', '+00:00'))
        return date.astimezone(timezone.utc).isoformat() if date.tzinfo else None


class ImportRequest(StrictModel):
    account_ref: str = Field(min_length=1, max_length=200)
    contact_ref: str = Field(min_length=1, max_length=200)
    mode: Literal['history', 'live'] = 'history'
    messages: list[Message] = Field(min_length=1, max_length=1000)


class Watch(StrictModel):
    user_text: str = Field(min_length=1, max_length=500)
    source_memory_id: int | None = Field(default=None, gt=0)

    @field_validator('user_text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('关注内容不能为空')
        return value.strip()


class WatchState(StrictModel):
    enabled: bool


class Mapping(StrictModel):
    kind: Literal['PERSON', 'PHONE', 'EMAIL', 'ADDRESS', 'ID']
    value: str = Field(min_length=2, max_length=500)


class Preview(StrictModel):
    text: str = Field(min_length=1, max_length=20000)
