import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import types

from telegram_mcp import runtime, video
from telegram_mcp.tools import media

FIXTURES = Path(__file__).parent / "fixtures"
LANDSCAPE = FIXTURES / "landscape_1920x1080.mp4"  # 2 s, 1920x1080
PORTRAIT = FIXTURES / "portrait_rotated_1920x1080.mp4"  # same frames, display matrix 90°

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed"
)


@pytest.fixture
def without_ffmpeg(monkeypatch):
    """The connectome image has no ffmpeg: only hachoir reads the file."""
    monkeypatch.setattr(video.shutil, "which", lambda name: None)


class _VideoClient:
    def __init__(self):
        self.calls = []
        self.uploads = []

    async def send_file(self, entity, file, **kwargs):
        thumb = kwargs.get("thumb")
        self.calls.append(
            {
                "file": file,
                **kwargs,
                "thumb_existed": bool(thumb) and Path(thumb).is_file(),
            }
        )
        if isinstance(file, list):
            return [SimpleNamespace(id=601 + i) for i in range(len(file))]
        return SimpleNamespace(id=601)

    async def upload_file(self, path):
        self.uploads.append(path)
        return types.InputFile(
            id=len(self.uploads), parts=1, name=Path(path).name, md5_checksum=""
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    for fixture in (LANDSCAPE, PORTRAIT):
        shutil.copy(fixture, tmp_path / fixture.name)
    (tmp_path / "photo.png").write_bytes(b"png")
    client = _VideoClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        return "entity"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    return client


def _video_attribute(attributes):
    (attribute,) = [a for a in attributes if isinstance(a, types.DocumentAttributeVideo)]
    return attribute


@pytest.mark.parametrize(
    ("name", "expected"),
    [("clip.mp4", True), ("clip.MOV", True), ("clip.m4v", True), ("clip.webm", True)]
    + [("photo.png", False), ("doc.pdf", False), ("anim.gif", False)],
)
def test_is_video(name, expected):
    assert video.is_video(name) is expected


@needs_ffmpeg
def test_ffprobe_reads_size_and_duration():
    assert video.probe_video(str(LANDSCAPE)) == video.VideoInfo(1920, 1080, 2.0)


@needs_ffmpeg
def test_ffprobe_applies_rotation():
    assert video.probe_video(str(PORTRAIT)) == video.VideoInfo(1080, 1920, 2.0)


def test_hachoir_reads_size_and_duration(without_ffmpeg):
    assert video.probe_video(str(LANDSCAPE)) == video.VideoInfo(1920, 1080, 2.0)


def test_hachoir_applies_rotation(without_ffmpeg):
    assert video.probe_video(str(PORTRAIT)) == video.VideoInfo(1080, 1920, 2.0)


def test_unreadable_video_has_no_info(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert video.probe_video(str(broken)) is None


@needs_ffmpeg
def test_thumbnail_is_at_most_320_px(tmp_path):
    from PIL import Image

    thumb = video.make_thumbnail(str(PORTRAIT), video.VideoInfo(1080, 1920, 2.0), str(tmp_path))
    with Image.open(thumb) as image:
        assert image.size == (180, 320)


def test_no_thumbnail_without_ffmpeg(tmp_path, without_ffmpeg):
    assert (
        video.make_thumbnail(str(LANDSCAPE), video.VideoInfo(1920, 1080, 2.0), str(tmp_path))
        is None
    )


@pytest.mark.asyncio
async def test_send_file_sends_1920x1080_video_with_its_size(client, without_ffmpeg):
    result = await media.send_file("chat", LANDSCAPE.name, caption="clip")

    assert result.startswith("File sent from")
    (call,) = client.calls
    attribute = _video_attribute(call["attributes"])
    assert (attribute.w, attribute.h, attribute.duration) == (1920, 1080, 2.0)
    assert attribute.supports_streaming
    assert call["supports_streaming"] is True
    assert "thumb" not in call


@needs_ffmpeg
@pytest.mark.asyncio
async def test_send_file_attaches_thumbnail_with_ffmpeg(client):
    await media.send_file("chat", PORTRAIT.name)

    (call,) = client.calls
    attribute = _video_attribute(call["attributes"])
    assert (attribute.w, attribute.h) == (1080, 1920)
    assert call["thumb_existed"]
    assert not Path(call["thumb"]).exists()  # temporary, removed after the send


@pytest.mark.asyncio
async def test_send_file_leaves_other_files_to_telethon(client):
    await media.send_file("chat", "photo.png")

    (call,) = client.calls
    assert set(call) == {"file", "caption", "reply_to", "schedule", "thumb_existed"}


@pytest.mark.asyncio
async def test_album_sends_each_video_with_its_size(client, tmp_path, without_ffmpeg):
    result = await media.send_album(
        "chat", [LANDSCAPE.name, "photo.png", PORTRAIT.name], caption="trip"
    )

    assert result.startswith("Album of 3 files sent")
    (call,) = client.calls
    landscape, photo, portrait = call["file"]
    assert photo == str(tmp_path / "photo.png")
    for item, size in ((landscape, (1920, 1080)), (portrait, (1080, 1920))):
        assert isinstance(item, types.InputMediaUploadedDocument)
        assert item.mime_type == "video/mp4"
        assert item.nosound_video
        attribute = _video_attribute(item.attributes)
        assert (attribute.w, attribute.h, attribute.duration) == (*size, 2.0)
        assert item.thumb is None
    assert client.uploads == [str(tmp_path / LANDSCAPE.name), str(tmp_path / PORTRAIT.name)]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_album_uploads_video_thumbnails_with_ffmpeg(client):
    await media.send_album("chat", [LANDSCAPE.name, PORTRAIT.name])

    (call,) = client.calls
    assert all(item.thumb is not None for item in call["file"])
    assert [Path(path).suffix for path in client.uploads] == [".mp4", ".jpg", ".mp4", ".jpg"]
