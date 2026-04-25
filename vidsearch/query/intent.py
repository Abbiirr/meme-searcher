import logging
import re

logger = logging.getLogger(__name__)

INTENT_CLASSES = ["exact_text", "fuzzy_text", "semantic_description", "mixed_visual_description"]


def classify_intent(query: str) -> str:
    q = query.strip().lower()

    quote_markers = ['"', "'", '"', '"', '\u2018', '\u2019']
    has_quotes = any(m in query for m in quote_markers)
    quoted_text = re.findall(r'[""\u201c\u201d\'](.+?)[""\u201c\u201d\']', query)

    strong_visual_keywords = [
        "template", "looks like", "picture of", "image of",
        "photo of", "drake", "doge", "cat", "woman", "man",
        "face", "reaction", "pointing", "sitting", "standing", "crying",
        "laughing", "holding", "wearing", "guy", "girl",
        "anime", "spider-man", "spiderman", "dog", "shiba",
    ]
    has_strong_visual = any(kw in q for kw in strong_visual_keywords)

    text_keywords = [
        "says", "text", "quote", "words", "reads", "written",
        "once again asking", "nobody", "none", "everybody",
    ]
    has_text_ref = any(kw in q for kw in text_keywords)

    semantic_keywords = [
        "meme about", "meme where", "meme that", "the one about",
        "the one that", "the one where", "meme for", "meme showing",
        "exhausted", "tired", "frustrated", "confused", "excited",
        "angry", "happy", "sad", "bored", "stressed",
    ]
    has_semantic = any(kw in q for kw in semantic_keywords)

    if (has_quotes or quoted_text) and has_strong_visual:
        return "mixed_visual_description"

    if has_quotes or quoted_text:
        return "exact_text"

    if has_strong_visual and has_text_ref:
        return "mixed_visual_description"

    if has_strong_visual and has_semantic:
        return "mixed_visual_description"

    if has_text_ref:
        if any(w in q for w in ["kinda", "sorta", "something like", "like", "maybe", "i think"]):
            return "fuzzy_text"
        return "exact_text"

    if has_semantic:
        return "semantic_description"

    if has_strong_visual:
        return "mixed_visual_description"

    if any(w in q for w in ["kinda", "sorta", "something like", "like", "maybe", "i think"]):
        return "fuzzy_text"

    return "semantic_description"
