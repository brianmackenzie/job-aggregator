"""Unit tests for src/scrapers/registry.py."""
import pytest

from scrapers.registry import (
    _reset_for_tests,
    get_scraper,
    list_scrapers,
    register,
)


@pytest.fixture(autouse=True)
def _clean_registry:
    """Each test starts with an empty registry and leaves it empty,
    so tests don't depend on import order or each other."""
    _reset_for_tests
    yield
    _reset_for_tests


def test_register_and_lookup:
    @register("foo")
    class Foo:
        pass

    assert get_scraper("foo") is Foo
    assert "foo" in list_scrapers


def test_register_duplicate_raises:
    @register("bar")
    class Bar1:
        pass

    with pytest.raises(ValueError, match="Duplicate"):
        @register("bar")
        class Bar2:
            pass


def test_register_same_class_twice_is_ok:
    """A module re-import (rare, but possible in tests) shouldn't crash."""
    @register("baz")
    class Baz:
        pass

    # Re-decorating the same class is treated as idempotent.
    register("baz")(Baz)
    assert get_scraper("baz") is Baz


def test_get_unknown_raises:
    with pytest.raises(KeyError, match="No scraper"):
        get_scraper("nonexistent")


def test_list_scrapers_sorted:
    @register("zeta")
    class Z: pass

    @register("alpha")
    class A: pass

    assert list_scrapers == ["alpha", "zeta"]
