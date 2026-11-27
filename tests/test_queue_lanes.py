from app.pipeline.queue import HEAVY, LIGHT, classify_row


def test_notes_and_links_are_light():
    assert classify_row({"type": "text"}) == LIGHT
    assert classify_row({"type": "link", "source": {"url": "https://example.org/post"}}) == LIGHT
    assert classify_row(None) == LIGHT


def test_video_and_audio_are_heavy():
    assert classify_row({"type": "video"}) == HEAVY
    assert classify_row({"type": "audio"}) == HEAVY
    assert classify_row({"type": "file", "mime": "audio/mpeg"}) == HEAVY


def test_video_links_are_heavy():
    for url in ("https://vk.com/video-1_2", "https://rutube.ru/video/abc/", "https://youtu.be/x"):
        assert classify_row({"type": "link", "source": {"url": url}}) == HEAVY


def test_big_files_are_heavy():
    assert classify_row({"type": "file", "size_bytes": 9 * 1024 * 1024}) == HEAVY
    assert classify_row({"type": "file", "size_bytes": 1024}) == LIGHT


def test_source_as_json_string():
    assert classify_row({"type": "link", "source": '{"url":"https://rutube.ru/video/x"}'}) == HEAVY
