from __future__ import annotations


from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

CaptionStyle = Literal["none", "standard", "animated"]


class CropRect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(default=1, gt=0, le=1, allow_inf_nan=False)
    height: float = Field(default=1, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def contained(self):
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("Crop must stay inside the source frame")
        return self


class LayoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["split", "blurred"] = "split"
    facecam: CropRect | None = None
    gameplay: CropRect = Field(default_factory=CropRect)
    split_ratio: float = Field(default=0.5, ge=0.2, le=0.7, allow_inf_nan=False)
    facecam_fit: Literal["fit", "fill"] = "fill"
    gameplay_fit: Literal["fit", "fill"] = "fit"
    caption_position: Literal["lower_middle", "center", "bottom"] = "lower_middle"


class SubtitleStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    font_family: Literal["Arial", "Impact", "Verdana"] = "Arial"
    font_size: float = Field(default=48, ge=18, le=96, allow_inf_nan=False)
    bold: bool = True
    text_color: str = "#FFFFFF"
    highlight_color: str = "#00FF00"
    outline_color: str = "#141414"
    outline_width: float = Field(default=3, ge=0, le=8, allow_inf_nan=False)
    background_color: str = "#000000"
    background_opacity: float = Field(default=0, ge=0, le=.85, allow_inf_nan=False)
    x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=.73, ge=.05, le=.95, allow_inf_nan=False)
    width: float = Field(default=.84, ge=.2, le=1, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def normalized(cls, value):
        from backend.processing.subtitle_style import normalize_subtitle_style
        return normalize_subtitle_style(value)


class AccountAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str | None = Field(default=None, min_length=1, max_length=200)


class IntegrationKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(max_length=4096)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_clips: int = Field(default=10, ge=1, le=50)
    sampling_interval: Literal[1, 2, 5, 10] = 5
    analysis_width: Literal[160, 320, 480] = 320
    audio_sample_rate: Literal[16000] = 16000
    cpu_workers: Literal[1, 2] = 2
    memory_mode: Literal["low", "balanced"] = "low"
    clip_duration: int = Field(default=35, ge=10, le=90)
    output_resolution: Literal["720x1280", "1080x1920"] = "720x1280"
    caption_style: CaptionStyle = "standard"
    ai_refinement: bool = False
    output_folder: str = Field(default="", max_length=2000)
    analysis_chunk_seconds: int = Field(default=300, ge=60, le=600)
    caption_provider: Literal["local", "groq"] = "local"
    default_layout: Literal["split", "blurred"] = "split"
    ai_scoring_provider: Literal["none", "gemini", "groq", "openai"] = "none"
    ai_scoring_model: str = Field(default="", max_length=120, pattern=r"^[A-Za-z0-9._/-]*$")
    ai_scoring_max_candidates: int = Field(default=5, ge=1, le=20)
    ai_scoring_frames: int = Field(default=2, ge=1, le=8)
    openai_auth_mode: Literal["api_key", "chatgpt"] = "api_key"
    ai_review_mode: Literal["text", "vision"] = "text"
    auto_ai_review: bool = Field(default=False, strict=True)
    ai_daily_request_limit: int = Field(default=20, ge=1, le=500, strict=True)
    ai_daily_budget_usd: float = Field(default=.10, ge=0, le=100, allow_inf_nan=False, strict=True)
    ai_max_output_tokens: int = Field(default=512, ge=128, le=2048, strict=True)
    ai_allow_unpriced_models: bool = Field(default=False, strict=True)
    cloud_consent: bool = False

    @field_validator("output_folder")
    @classmethod
    def absolute_output(cls, value: str) -> str:
        from pathlib import Path
        value = value.strip()
        if value and not Path(value).is_absolute():
            raise ValueError("Use an absolute output folder path, or leave it empty")
        return value


class LocalImport(BaseModel):
    path: str = Field(min_length=1, max_length=4000)


class YouTubeImport(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class AnalyzeRequest(BaseModel):
    force: bool = False
    auto_enhance: bool = True


class ExportRequest(BaseModel):
    caption_style: CaptionStyle | None = None


class BatchExport(ExportRequest):
    highlight_ids: list[str] = Field(min_length=1, max_length=50)


class Word(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    word: str = Field(min_length=1, max_length=150)

    @model_validator(mode="after")
    def interval(self):
        if self.end <= self.start:
            raise ValueError("Word end must follow its start")
        return self


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=2000)
    words: list[Word] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def interval(self):
        if self.end <= self.start:
            raise ValueError("Caption end must follow its start")
        if any(w.start < self.start or w.end > self.end + 0.01 for w in self.words):
            raise ValueError("Word timestamps must be within their caption segment")
        if any(b.start < a.start for a, b in zip(self.words, self.words[1:])):
            raise ValueError("Word timestamps must be ordered")
        return self


class HighlightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    transcript: list[Segment] | None = Field(default=None, max_length=200)
    subtitle_style: SubtitleStyle | None = None
