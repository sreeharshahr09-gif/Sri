"""Source-first discovery: publisher feeds, dated news search, paper metadata.

Every candidate carries a publication date from a source that states one
(feed item, news index, bibliographic API, page metadata, or a dated URL).
Undated candidates and candidates outside the edition window are dropped
before any page is fetched or any model call is made.
"""
from .window import EditionWindow, edition_window
from .candidates import Candidate

__all__ = ["Candidate", "EditionWindow", "edition_window"]
