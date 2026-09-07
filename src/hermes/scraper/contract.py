"""Scraper service canonical data contract.

Pydantic models for scrape tasks, results, and source type classification.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ScrapeSource(str, Enum):
    WEB = "web"
    PDF = "pdf"
    VENDOR_CATALOG = "vendor_catalog"


class ScrapeTask(BaseModel):
    task_id: str
    keys: list[str] = Field(default_factory=list)
    source_type: ScrapeSource = ScrapeSource.WEB
    policy: dict = Field(default_factory=dict)
    task_type: Literal["scrape"] = "scrape"


class ScrapeResult(BaseModel):
    task_id: str
    ok: bool = True
    chunks_stored: int = 0
    uris: list[str] = Field(default_factory=list)
    error: str = ""
    source_type: ScrapeSource = ScrapeSource.WEB
