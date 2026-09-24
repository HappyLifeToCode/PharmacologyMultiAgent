import pharm.diseases.omim_online as omim_online


def test_omim_assist_wait(monkeypatch):
    responses = iter([
        {"request_id": "omim-1"},
        {"state": "pending", "pending": {"request_id": "omim-1"}},
        {"state": "done", "pending": None},
    ])
    monkeypatch.setattr(omim_online, "_assist_json", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(omim_online.time, "sleep", lambda _: None)
    meta = {"actions": []}
    omim_online.wait_for_assist("http://127.0.0.1:8766", "https://www.omim.org/search?search=X", "X", meta)
    assert [item["action"] for item in meta["actions"]] == ["assist_requested", "assist_completed"]
