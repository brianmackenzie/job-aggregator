"""Scraper plugin registry.

Usage:
    from scrapers.base import BaseScraper
    from scrapers.registry import register, get_scraper

    @register("remoteok")
    class RemoteOKScraper(BaseScraper):
        source_name = "remoteok"
        schedule = "cron(0 6 * * ? *)"
        ...

    cls = get_scraper("remoteok")
    cls.scrape_run

The worker Lambda uses get_scraper to resolve a class by name from
the event payload. Scraper modules must be imported somewhere for
their @register decorator to fire — the worker Lambda does this at
module import time so all scrapers are registered by first invocation.
"""
from typing import Callable, Type

_registry: dict[str, type] = {}


def register(source_name: str) -> Callable[[Type], Type]:
    """Class decorator that records a BaseScraper subclass in the registry."""
    def _wrap(cls: Type) -> Type:
        if source_name in _registry and _registry[source_name] is not cls:
            raise ValueError(
                f"Duplicate scraper registration for {source_name!r}: "
                f"{_registry[source_name].__name__} vs {cls.__name__}"
            )
        _registry[source_name] = cls
        return cls
    return _wrap


def get_scraper(source_name: str) -> type:
    """Return the scraper class registered for the given name.
    Raises KeyError if not found."""
    if source_name not in _registry:
        raise KeyError(
            f"No scraper registered for {source_name!r}. "
            f"Known: {sorted(_registry.keys)}"
        )
    return _registry[source_name]


def list_scrapers -> list[str]:
    """All registered scraper names. Used by health/debug endpoints."""
    return sorted(_registry.keys)


def _reset_for_tests -> None:
    """Drop the registry — used between tests so each test starts clean."""
    _registry.clear
