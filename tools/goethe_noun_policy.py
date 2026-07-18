"""Shared fail-closed article and gender policy for Goethe nouns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class NounPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ArticlelessNounException:
    source_id: str
    lemma: str
    gender: str
    duden_url: str


ARTICLELESS_NOUN_EXCEPTIONS = {
    item.source_id: item
    for item in (
        ArticlelessNounException(
            "A1-WG-0105", "Deutschland", "n.",
            "https://www.duden.de/rechtschreibung/Deutschland",
        ),
        ArticlelessNounException(
            "A1-WG-0106", "Europa", "n.",
            "https://www.duden.de/rechtschreibung/Europa_Kontinent",
        ),
        ArticlelessNounException(
            "A1-WG-0108", "Finnland", "n.",
            "https://www.duden.de/rechtschreibung/Finnland",
        ),
        ArticlelessNounException(
            "A1-WG-0109", "Mexiko", "n.",
            "https://www.duden.de/rechtschreibung/Mexiko_Staat",
        ),
        ArticlelessNounException(
            "A2-WG-0088", "Österreich", "n.",
            "https://www.duden.de/rechtschreibung/Oesterreich",
        ),
        ArticlelessNounException(
            "A2-WG-0090", "Luxemburg", "n.",
            "https://www.duden.de/rechtschreibung/Luxemburg_Staat",
        ),
    )
}

ARTICLE_GENDER = {"der": "m.", "die": "f.", "das": "n."}


def exception_ids_for_level(level: str) -> frozenset[str]:
    prefix = f"{level}-WG-"
    return frozenset(
        source_id for source_id in ARTICLELESS_NOUN_EXCEPTIONS
        if source_id.startswith(prefix)
    )


def validate_noun_article(
    *,
    source_id: str,
    lemma: str,
    pos: str,
    article: str,
    gender: str,
    dictionary_sources: Iterable[str] | None = None,
    require_complete_mapping: bool = True,
) -> bool:
    """Validate one noun and return whether it uses a reviewed exception.

    ``dictionary_sources=None`` is intended for derived manifests, whose source
    rows have already been validated. Source validators should pass the URLs so
    an exception also proves its exact reviewed Duden evidence.
    """
    if pos.strip() != "n.":
        return False

    source_id = source_id.strip()
    lemma = lemma.strip()
    article = article.strip()
    gender = gender.strip()
    exception = ARTICLELESS_NOUN_EXCEPTIONS.get(source_id)

    if exception and lemma != exception.lemma:
        raise NounPolicyError(
            f"articleless noun exception identity mismatch for {source_id}: {lemma!r}"
        )
    if not gender:
        raise NounPolicyError("enriched noun is missing Gender")

    if not article:
        if not exception:
            raise NounPolicyError(
                f"noun is missing Article and is not a reviewed exception: {source_id} {lemma!r}"
            )
        if gender != exception.gender:
            raise NounPolicyError(
                f"articleless noun exception has stale Gender: {gender!r}"
            )
        if dictionary_sources is not None:
            sources = {item.strip() for item in dictionary_sources if item.strip()}
            if exception.duden_url not in sources:
                raise NounPolicyError(
                    f"articleless noun exception is missing Duden evidence: {exception.duden_url}"
                )
        return True

    if exception:
        raise NounPolicyError(
            f"articleless noun exception is stale for {source_id}: Article is {article!r}"
        )

    articles = article.split("/")
    genders = gender.split("/")
    if gender == "pl.":
        valid = articles == ["die"]
    else:
        valid = len(set(articles)) == len(articles) and all(
            ARTICLE_GENDER.get(item) == genders[index]
            for index, item in enumerate(articles)
        ) if len(articles) == len(genders) else False
        if not require_complete_mapping:
            valid = len(set(articles)) == len(articles) and all(
                ARTICLE_GENDER.get(item) in genders for item in articles
            )
    if not valid:
        raise NounPolicyError(f"Article/Gender mismatch {article!r}/{gender!r}")
    return False
