"""Jobboard scrapers package."""

from .base import BaseScraper
from .hellowork import HelloWorkScraper
from .indeed import IndeedScraper
from .linkedin import LinkedInScraper
from .wttj import WTTJScraper

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "wttj": WTTJScraper,
    "hellowork": HelloWorkScraper,
    "indeed": IndeedScraper,
    "linkedin": LinkedInScraper,
}

__all__ = [
    "BaseScraper",
    "HelloWorkScraper",
    "IndeedScraper",
    "LinkedInScraper",
    "SCRAPER_REGISTRY",
    "WTTJScraper",
]
