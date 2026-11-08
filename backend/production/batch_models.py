from typing import Literal
from pydantic import BaseModel, Field


class Brand(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: str = Field(min_length=1, max_length=100)
    pack_id: str = Field(default='roundtable', pattern=r'^[a-z0-9-]{1,60}$')
    notes: str = Field(default='', max_length=20000)
    outro_ids: list[str] = Field(default_factory=list, max_length=100)
    material_ids: list[str] = Field(default_factory=list, max_length=10000)
    sticker_ids: list[str] = Field(default_factory=list, max_length=100)
    voice_profile: str | None = None
    tts_engine: str | None = None


class BatchRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=100)
    brand_id: str
    workflow: Literal['product', 'talking_head', 'narration', 'intake'] = 'product'
    source_ids: list[str] = Field(min_length=1, max_length=1000)
    instruction: str = Field(default='Start. Follow the configured workflow for every pending input.', max_length=20000)
    retry_of: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


WORKFLOW_DIRS = {
    'product': '0_inbox_\u4ea7\u54c1',
    'talking_head': '0_inbox_\u53e3\u64ad',
    'narration': '0_inbox_\u914d\u97f3',
    'intake': '1_assets/_intake',
}
