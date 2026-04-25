from vidsearch.query.intent import classify_intent, INTENT_CLASSES


def test_exact_text_with_quotes():
    assert classify_intent('the meme that says "i am once again asking"') == "exact_text"


def test_mixed_visual_drake():
    assert classify_intent("the drake meme about code review") == "mixed_visual_description"


def test_semantic_description():
    assert classify_intent("the meme where someone looks exhausted and done with life") == "semantic_description"


def test_mixed_visual_doge():
    assert classify_intent("the meme with the doge shiba inu") == "mixed_visual_description"


def test_exact_text_says():
    assert classify_intent("the meme that says nobody came to my birthday") == "exact_text"


def test_semantic_about():
    assert classify_intent("meme about how i feel when code works") == "semantic_description"


def test_mixed_visual_anime():
    assert classify_intent("the meme about an anime handshake") == "mixed_visual_description"


def test_fuzzy_text():
    result = classify_intent("i think it was like the distracted boyfriend meme or something")
    assert result in INTENT_CLASSES


def test_all_classes_are_valid():
    for cls in INTENT_CLASSES:
        assert cls in ["exact_text", "fuzzy_text", "semantic_description", "mixed_visual_description"]
