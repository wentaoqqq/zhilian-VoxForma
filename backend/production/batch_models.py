from typing import Literal
from pydantic import BaseModel, Field, model_validator


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
    brand_id: str | None = None  # Old batches retain their original meaning.
    product_id: str | None = None
    video_type_id: str | None = None
    conversation_id: str | None = None
    workflow: Literal['product', 'talking_head', 'narration', 'intake'] = 'product'
    source_ids: list[str] = Field(default_factory=list, max_length=1000)
    instruction: str = Field(default='Start. Follow the configured workflow for every pending input.', max_length=20000)
    retry_of: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')

    @model_validator(mode='after')
    def check_context(self):
        if bool(self.product_id) == bool(self.brand_id):
            raise ValueError('Choose a product. Legacy requests may supply a brand instead.')
        if self.product_id and not self.video_type_id:
            raise ValueError('Choose a video type for this product.')
        if not self.source_ids and not self.conversation_id:
            raise ValueError('Select at least one input outside a conversation.')
        return self


WORKFLOW_DIRS = {
    'product': '0_inbox_\u4ea7\u54c1',
    'talking_head': '0_inbox_\u53e3\u64ad',
    'narration': '0_inbox_\u914d\u97f3',
    'intake': '1_assets/_intake',
}
