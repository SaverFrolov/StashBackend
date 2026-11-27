import shutil

from app.pipeline.video.runner import _lowprio


def test_argv_preserved(monkeypatch):
    monkeypatch.setenv("VIDEO_CHILD_NICE", "19")
    monkeypatch.setenv("VIDEO_CHILD_IONICE", "3")
    monkeypatch.setenv("VIDEO_CHILD_CPUS", "8-19")
    out = _lowprio(["python", "transcribe.py", "--threads", "6"])
    assert out[-4:] == ["python", "transcribe.py", "--threads", "6"]
    if shutil.which("nice"):
        assert out[:3] == ["nice", "-n", "19"]
    if shutil.which("taskset"):
        assert "taskset" in out and "8-19" in out


def test_disabled(monkeypatch):
    monkeypatch.setenv("VIDEO_CHILD_NICE", "")
    monkeypatch.setenv("VIDEO_CHILD_IONICE", "")
    monkeypatch.setenv("VIDEO_CHILD_CPUS", "")
    assert _lowprio(["ffmpeg", "-i", "x"]) == ["ffmpeg", "-i", "x"]
