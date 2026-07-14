from __future__ import annotations

from xml.etree import ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException


class SourceXmlError(ValueError):
    pass


def parse_untrusted_xml(content: bytes | str) -> ElementTree.Element:
    try:
        return DefusedElementTree.fromstring(
            content,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (
        DefusedXmlException,
        ElementTree.ParseError,
        LookupError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise SourceXmlError("Source XML contains unsafe or invalid markup.") from exc
