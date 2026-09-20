from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchLocale:
    label: str
    language: str
    hl: str
    gl: str
    location: str | None = None

    @property
    def cache_identity(self) -> str:
        return f"{self.label}:{self.hl}:{self.gl or '-'}:v1"


LEGACY_LOCALE = SearchLocale("legacy", "en", "", "")

LOCALES: dict[str, SearchLocale] = {
    locale.label: locale
    for locale in (
        SearchLocale("zh-CN-CN", "zh", "zh-CN", "cn"),
        SearchLocale("zh-CN-HK", "zh", "zh-CN", "hk"),
        SearchLocale("zh-TW-TW", "zh", "zh-TW", "tw"),
        SearchLocale("zh-CN-SG", "zh", "zh-CN", "sg"),
        SearchLocale("zh-CN-US", "zh", "zh-CN", "us"),
        SearchLocale("en-SG-SG", "en", "en", "sg"),
        SearchLocale("en-US-US", "en", "en", "us"),
        SearchLocale("en-GB-GB", "en", "en", "gb"),
        SearchLocale("en-IN-IN", "en", "en", "in"),
    )
}

DEFAULT_LOCALES = tuple(LOCALES.values())


def runner_locales(store):
    from .google_experiment import normalize_languages

    return parse_locales(store.get('locales')) if store.get('locales') else tuple(
        locale_for_language(language) for language in normalize_languages(store.get('languages'))
    )


def locales_for_language(language: str) -> tuple[SearchLocale, ...]:
    return tuple(locale for locale in DEFAULT_LOCALES if locale.language == language)


def default_locales_for_languages(languages: tuple[str, ...]) -> tuple[SearchLocale, ...]:
    """Expand the explicit auto setting in stable language order."""
    selected: list[SearchLocale] = []
    for language in languages:
        for locale in locales_for_language(language):
            if locale not in selected:
                selected.append(locale)
    return tuple(selected)


def parse_locales(value: str | list[str] | tuple[str, ...]) -> tuple[SearchLocale, ...]:
    if value is None:
        raise ValueError("locales must not be empty")
    labels = value.split(",") if isinstance(value, str) else value
    selected: list[SearchLocale] = []
    for label in labels:
        key = str(label).strip()
        if not key:
            continue
        locale = LOCALES.get(key)
        if locale is None:
            raise ValueError(f"unknown locale: {key}")
        if locale not in selected:
            selected.append(locale)
    if not selected:
        raise ValueError("locales must not be empty")
    return tuple(selected)


def locale_for_language(language: str) -> SearchLocale:
    """Return the nearest non-matrix locale for existing production behavior."""
    if language == "zh":
        return SearchLocale("legacy", language, "zh-CN", "")
    return SearchLocale("legacy", language, "en", "")
