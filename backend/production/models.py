from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Caption(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_range(self):
        if self.end <= self.start:
            raise ValueError("Subtitle end must follow its start.")
        return self


class ProductDraft(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    schema_version: Literal[1] = 1
    source_id: str
    outro_id: str
    plugin_id: str = Field(default="general", pattern=r"^[a-z0-9-]{1,60}$")
    title: str = Field(default="", max_length=120)
    cover_title: str = Field(default="", max_length=120)
    publication_title: str = Field(default="", max_length=500)
    brand_text: str = Field(default="", max_length=80)
    subtitle_mode: Literal["keep", "none", "provided"] = "keep"
    captions: list[Caption] = Field(default_factory=list, max_length=10000)
    cover_time: float = Field(default=0, ge=0)


class ProductPlan(ProductDraft):
    @model_validator(mode="after")
    def check_text(self):
        for key in ("title", "cover_title", "publication_title"):
            if not getattr(self, key).strip():
                raise ValueError(f"{key} cannot be blank.")
        if self.subtitle_mode == "provided" and not self.captions:
            raise ValueError("Provide timed subtitles or choose another subtitle mode.")
        return self
