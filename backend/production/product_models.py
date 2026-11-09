"""Product configuration; material categories and video types are independent."""
from __future__ import annotations

from typing import Literal
import uuid

from pydantic import BaseModel, Field, model_validator

Workflow = Literal['product', 'talking_head', 'narration']
AssetRole = Literal['input', 'material', 'outro', 'sticker']


class Category(BaseModel):
    model_config = {'str_strip_whitespace': True}
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=20000)


class VideoType(BaseModel):
    model_config = {'str_strip_whitespace': True}
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    workflow: Workflow
    instruction: str = Field(default='', max_length=20000)


def default_video_types() -> list[VideoType]:
    return [VideoType(id=id_, name=name, workflow=id_) for id_, name in (
        ('product', 'Product'), ('talking_head', 'Talking head'), ('narration', 'Narration'),
    )]


class Product(BaseModel):
    model_config = {'str_strip_whitespace': True}
    id: str | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default='', max_length=20000)
    pack_id: str = Field(default='roundtable', pattern=r'^[a-z0-9-]{1,60}$')
    outro_ids: list[str] = Field(default_factory=list, max_length=100)
    material_ids: list[str] = Field(default_factory=list, max_length=10000)
    sticker_ids: list[str] = Field(default_factory=list, max_length=100)
    voice_profile: str | None = None
    tts_engine: str | None = None
    categories: list[Category] = Field(default_factory=list, max_length=1000)
    video_types: list[VideoType] = Field(default_factory=default_video_types, max_length=1000)

    @model_validator(mode='after')
    def unique_references(self):
        for values in (self.categories, self.video_types):
            if len({value.id for value in values}) != len(values):
                raise ValueError('Category and video type IDs must be unique within their list.')
        resources = self.outro_ids + self.material_ids + self.sticker_ids
        if len(set(resources)) != len(resources):
            raise ValueError('Each resource must have exactly one role in a product.')
        return self


class AssetUpdate(BaseModel):
    model_config = {'extra': 'forbid', 'str_strip_whitespace': True}
    category_id: str | None = Field(default=None, max_length=100)
    video_type_id: str = Field(default='', max_length=100)
    role: AssetRole = 'input'
