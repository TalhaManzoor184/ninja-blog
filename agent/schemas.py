"""Structured output the writer must return."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Step(BaseModel):
    title: str = Field(description="Short imperative step title, e.g. 'Open the solution in make.powerapps.com'")
    detail: str = Field(description="1-3 sentences of plain text explaining exactly what to click, type or check. Inline `code` allowed with backticks.")
    code: Optional[str] = Field(default=None, description="Optional code, formula or command for this step")
    code_language: Optional[str] = Field(default=None, description="e.g. powerfx, javascript, csharp, powershell, json, xml")


class Block(BaseModel):
    type: Literal["paragraph", "steps", "list", "code", "note", "table", "diagram", "image"]
    text: Optional[str] = Field(default=None, description="paragraph / note text (plain text, `code` and **bold** allowed)")
    items: Optional[list[str]] = Field(default=None, description="list items (plain text)")
    steps: Optional[list[Step]] = None
    code: Optional[str] = None
    code_language: Optional[str] = None
    table_header: Optional[list[str]] = None
    table_rows: Optional[list[list[str]]] = None
    mermaid: Optional[str] = Field(default=None, description="diagram only: Mermaid 'flowchart LR' or 'flowchart TD' source, short labels, max 8 nodes")
    image_prompt: Optional[str] = Field(default=None, description="image only: description of a conceptual illustration. No text, no logos, no fake screenshots.")
    caption: Optional[str] = Field(default=None, description="diagram/image caption, also used as alt text")


class Section(BaseModel):
    heading: str = Field(description="H2 heading that answers a real search question")
    blocks: list[Block]


class FAQ(BaseModel):
    q: str
    a: str


class Article(BaseModel):
    keep: bool = Field(description="false only if the topic has no lasting value for this site's audience (e.g. a dead news item); then explain in quality_notes")
    seo_title: str = Field(description="50-60 characters, primary keyword first, specific benefit, no site name")
    h1: str = Field(description="The visible post title; can be a little longer than seo_title")
    meta_description: str = Field(description="130-155 characters, says what the reader gets, ends with a reason to click")
    focus_keyword: str
    intro: list[str] = Field(description="2-3 short paragraphs. The first paragraph answers the main question directly.")
    sections: list[Section]
    key_takeaways: list[str] = Field(description="3-5 one-line takeaways")
    faqs: list[FAQ] = Field(description="3-5 real questions people search for, answered in 1-3 sentences")
    category: str = Field(description="Exactly one name from the provided category list")
    tags: list[str] = Field(description="3-5 short tags")
    featured_image_prompt: str = Field(description="Conceptual illustration for the header image. No text, no letters, no logos, no UI screenshots.")
    featured_image_alt: str
    internal_links_used: list[str] = Field(default_factory=list, description="URLs of this site you linked to")
    quality_notes: str = Field(description="What you changed, anything from the original you removed as outdated, and anything a human should double-check")


class Research(BaseModel):
    current_facts: list[str] = Field(description="Up-to-date facts, names and menu paths relevant to the topic (e.g. Azure AD is now Microsoft Entra ID)")
    outdated_in_original: list[str] = Field(description="Things in the original post that are no longer correct")
    search_questions: list[str] = Field(description="Questions people actually search about this topic")
    reference_urls: list[str] = Field(default_factory=list, description="Official Microsoft Learn URLs used")
