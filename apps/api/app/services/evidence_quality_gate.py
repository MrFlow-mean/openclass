from __future__ import annotations

import re

from app.models import RetrievalEvidence


def filter_relevant_local_evidence(
    *,
    query: str,
    evidence: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    terms = _significant_terms(query)
    if not terms:
        return []
    required_matches = 1 if len(terms) <= 2 else 2
    accepted: list[RetrievalEvidence] = []
    for item in evidence:
        searchable = " ".join(
            [
                item.source_title,
                " ".join(item.section_path),
                item.excerpt,
                item.expanded_text[:1200],
            ]
        ).casefold()
        matched_terms = {term for term in terms if term.casefold() in searchable}
        if len(matched_terms) < required_matches:
            continue
        accepted.append(
            item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "quality_gate": {
                            "accepted": True,
                            "matched_terms": sorted(matched_terms),
                            "required_matches": required_matches,
                        },
                    }
                }
            )
        )
    return accepted


def _significant_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", query.casefold()):
        if len(token) >= 2:
            terms.append(token)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        if len(sequence) <= 8:
            terms.append(sequence)
        if len(sequence) >= 4:
            terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    seen: set[str] = set()
    return [term for term in terms if not (term in seen or seen.add(term))]
