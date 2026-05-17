"""Compiled regex patterns for toolset routing rules.

Extracted from tools/toolsets.py to keep the toolset manifest clean.
Each constant corresponds to one toolset that uses a regex-based RoutingRule.
"""

import re

ANALYSIS_PATTERN = re.compile(
    r"\bwhat\s+(?:kind|type|sort)\s+of\s+(?:file|binary|program)\b"
    r"|\bwhat\s+is\s+this\s+(?:file|binary|program)\b"
    r"|\bfile\s+type\b"
    r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:file\s+)?(?:type|format|architecture)\b",
    re.IGNORECASE,
)

SEARCH_PATTERN = re.compile(
    r"\bsearch\s+(?:the\s+)?(?:web|internet|online)\b"
    r"|\bfind\s+(?:me\s+)?(?:information|articles|images|news)\b"
    r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:latest|current)\b"
    r"|\blook\s+(?:it\s+)?up\b",
    re.IGNORECASE,
)

GIT_PATTERN = re.compile(
    r"\bgit\s+\w+\b|\bcommit\s+history\b|\bworking\s+tree\b"
    r"|\bwho\s+(?:wrote|added|changed|modified)\b",
    re.IGNORECASE,
)

BRIEFBOT_PATTERN = re.compile(
    r"\b(?:find|search\s+for|look\s+for)\s+(?:research\s+)?papers?\b"
    r"|\bwhat(?:'s|'s| is)\s+(?:trending|hot|new)\s+in\b"
    r"|\brecent\s+(?:papers?|research|articles?)\s+(?:on|about)\b"
    r"|\blatest\s+(?:research|papers?|developments?)\s+(?:on|in|about)\b",
    re.IGNORECASE,
)

REVERSING_PATTERN = re.compile(
    r"\bwhat\s+functions?\s+(?:exist|are|does)\b"
    r"|\bhow\s+does\s+\w+\s+call\b"
    r"|\bcall\s+(?:graph|chain|tree)\b"
    r"|\bdecompil(?:e|ed|ing)\b"
    r"|\bfunction\s+(?:list|inventory|map)\b",
    re.IGNORECASE,
)

SYMBOLIC_PATTERN = re.compile(
    r"\bcan\s+execution\s+reach\b"
    r"|\bwhat\s+input\s+(?:reaches|triggers|causes)\b"
    r"|\bsolve\s+(?:the\s+)?(?:password|key|checksum|crackme)\b"
    r"|\bprove\s+(?:this|the)\s+buffer\b"
    r"|\bfind\s+(?:an?\s+)?input\s+that\b",
    re.IGNORECASE,
)

CONTAINER_PATTERN = re.compile(
    r"\biterate\s+on\s+(?:the\s+)?(?:code|source|clone|reconstruction)\b"
    r"|\bverify\s+(?:the\s+)?reconstruction\b"
    r"|\btest\s+(?:the\s+)?(?:clone|reconstruction)\s+against\b"
    r"|\bdoes\s+\S+\s+match\s+(?:the\s+)?(?:original|binary)\b",
    re.IGNORECASE,
)
