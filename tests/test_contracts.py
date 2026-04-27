from vidsearch.api.contracts import SearchRequest, SearchResponse, SearchHit


def test_search_request_defaults():
    req = SearchRequest(query="test")
    assert req.limit == 10
    assert req.include_debug is False


def test_search_response_roundtrip():
    hit = SearchHit(
        rank=1,
        image_id="img_abc123",
        source_uri="data/meme/test.jpg",
        thumbnail_uri="minio://thumbnails/ab/img_abc123.jpg",
        ocr_excerpt="hello world",
        retrieval_score=0.95,
        rerank_score=0.92,
    )
    resp = SearchResponse(
        query="test query",
        intent="exact_text",
        total_returned=1,
        hits=[hit],
    )
    data = resp.model_dump()
    resp2 = SearchResponse.model_validate(data)
    assert resp2.query == "test query"
    assert resp2.hits[0].image_id == "img_abc123"
    assert resp2.hits[0].rerank_score == 0.92


def test_search_request_validation():
    req = SearchRequest(query="test", limit=100)
    assert req.limit == 100

    try:
        SearchRequest(query="")
        assert False, "should raise"
    except Exception:
        pass

    try:
        SearchRequest(query="test", limit=0)
        assert False, "should raise"
    except Exception:
        pass

    try:
        SearchRequest(query="test", limit=101)
        assert False, "should raise"
    except Exception:
        pass
