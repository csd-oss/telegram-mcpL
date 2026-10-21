import io
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from telethon import types

from telegram_mcp import runtime, video
from telegram_mcp.tools import media

FIXTURES = Path(__file__).parent / "fixtures"
LANDSCAPE = FIXTURES / "landscape_1920x1080.mp4"  # 2 s, 1920x1080
PORTRAIT = FIXTURES / "portrait_rotated_1920x1080.mp4"  # same frames, display matrix 90°
# 1440x1080 coded with 4:3 pixels: shown as 1920x1080
ANAMORPHIC_MP4 = FIXTURES / "anamorphic_1440x1080_sar4-3.mp4"
ANAMORPHIC_WEBM = FIXTURES / "anamorphic_1440x1080_sar4-3.webm"

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
        self.calls.append({"file": file, **kwargs})
        if isinstance(file, list):
            return [SimpleNamespace(id=601 + i) for i in range(len(file))]
        return SimpleNamespace(id=601)

    async def upload_file(self, file, file_name=None):
        self.uploads.append(file)
        name = file_name or Path(file).name
        return types.InputFile(id=len(self.uploads), parts=1, name=name, md5_checksum="")


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


def _assert_video(item, size, file_name):
    assert isinstance(item, types.InputMediaUploadedDocument)
    assert item.mime_type == "video/mp4"
    assert item.nosound_video
    filename, attribute = item.attributes
    assert filename == types.DocumentAttributeFilename(file_name)
    assert isinstance(attribute, types.DocumentAttributeVideo)
    assert (attribute.w, attribute.h, attribute.duration) == (*size, 2.0)
    assert attribute.supports_streaming


@pytest.mark.parametrize(
    ("name", "expected"),
    [("clip.mp4", True), ("clip.MOV", True), ("clip.m4v", True), ("clip.webm", True)]
    + [("photo.png", False), ("doc.pdf", False), ("anim.gif", False)],
)
def test_is_video(name, expected):
    assert video.is_video(name) is expected


@needs_ffmpeg
@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (LANDSCAPE, (1920, 1080)),
        (PORTRAIT, (1080, 1920)),
        (ANAMORPHIC_MP4, (1920, 1080)),
        (ANAMORPHIC_WEBM, (1920, 1080)),
    ],
)
def test_ffprobe_reads_displayed_size_and_duration(fixture, expected):
    assert video._probe_ffprobe(str(fixture)) == video.VideoInfo(*expected, 2.0)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (LANDSCAPE, (1920, 1080)),
        (PORTRAIT, (1080, 1920)),
        (ANAMORPHIC_MP4, (1920, 1080)),
        (ANAMORPHIC_WEBM, (1920, 1080)),
    ],
)
def test_hachoir_reads_displayed_size_and_duration(fixture, expected, without_ffmpeg):
    assert video.probe_video(str(fixture)) == video.VideoInfo(*expected, 2.0)


def test_hachoir_defaults_a_missing_matroska_display_height(tmp_path, without_ffmpeg):
    """Matroska may leave DisplayHeight out; it then defaults to the coded height."""
    display_height = bytes.fromhex("54ba820438")  # DisplayHeight = 1080
    void = bytes.fromhex("ec83000000")  # Void element of the same length
    data = ANAMORPHIC_WEBM.read_bytes()
    assert data.count(display_height) == 1
    trimmed = tmp_path / "trimmed.webm"
    trimmed.write_bytes(data.replace(display_height, void))

    assert video.probe_video(str(trimmed)) == video.VideoInfo(1920, 1080, 2.0)


def test_unreadable_video_has_no_info(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert video.probe_video(str(broken)) is None


@needs_ffmpeg
def test_ffmpeg_does_not_follow_a_concat_manifest(tmp_path):
    """A manifest named .mp4 must not make ffmpeg read (and thumbnail) another file."""
    shutil.copy(LANDSCAPE, tmp_path / "elsewhere.mp4")
    manifest = tmp_path / "clip.mp4"
    manifest.write_text("ffconcat version 1.0\nfile elsewhere.mp4\n")

    assert video._probe_ffprobe(str(manifest)) is None
    assert video.thumbnail_jpeg(str(manifest), video.VideoInfo(1920, 1080, 2.0)) is None


@needs_ffmpeg
@pytest.mark.parametrize(
    ("fixture", "expected"),
    [(PORTRAIT, (180, 320)), (ANAMORPHIC_MP4, (320, 180))],
)
def test_thumbnail_is_the_displayed_shape_within_320_px(fixture, expected):
    thumb = video.thumbnail_jpeg(str(fixture), video.probe_video(str(fixture)))
    with Image.open(io.BytesIO(thumb)) as image:
        assert image.format == "JPEG"
        assert image.size == expected


def test_no_thumbnail_without_ffmpeg(without_ffmpeg):
    assert video.thumbnail_jpeg(str(LANDSCAPE), video.VideoInfo(1920, 1080, 2.0)) is None


@pytest.mark.asyncio
async def test_send_file_sends_1920x1080_video_with_its_size(client, tmp_path, without_ffmpeg):
    result = await media.send_file("chat", LANDSCAPE.name, caption="clip")

    assert result.startswith("File sent from")
    (call,) = client.calls
    _assert_video(call["file"], (1920, 1080), LANDSCAPE.name)
    assert call["file"].thumb is None
    assert call["caption"] == "clip"
    assert client.uploads == [str(tmp_path / LANDSCAPE.name)]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_send_file_attaches_thumbnail_with_ffmpeg(client):
    await media.send_file("chat", PORTRAIT.name)

    (call,) = client.calls
    _assert_video(call["file"], (1080, 1920), PORTRAIT.name)
    assert call["file"].thumb.name == "thumb.jpg"
    assert isinstance(client.uploads[1], bytes)


@pytest.mark.asyncio
async def test_send_file_leaves_other_files_to_telethon(client, tmp_path):
    await media.send_file("chat", "photo.png")

    (call,) = client.calls
    assert call["file"] == str(tmp_path / "photo.png")
    assert client.uploads == []


@pytest.mark.asyncio
async def test_unreadable_video_gets_telethons_attributes_off_the_loop(client, monkeypatch):
    (Path(runtime.SERVER_ALLOWED_ROOTS[0]) / "broken.mp4").write_bytes(b"not a video")
    threads = []
    get_attributes = media.utils.get_attributes

    def _recording_get_attributes(*args, **kwargs):
        threads.append(threading.current_thread())
        return get_attributes(*args, **kwargs)

    monkeypatch.setattr(media.utils, "get_attributes", _recording_get_attributes)

    await media.send_file("chat", "broken.mp4")

    (call,) = client.calls
    assert call["file"].mime_type == "video/mp4"
    assert types.DocumentAttributeFilename("broken.mp4") in call["file"].attributes
    assert threads and threading.main_thread() not in threads


@pytest.mark.asyncio
async def test_album_sends_each_video_with_its_size(client, tmp_path, without_ffmpeg):
    result = await media.send_album(
        "chat", [LANDSCAPE.name, "photo.png", PORTRAIT.name], caption="trip"
    )

    assert result.startswith("Album of 3 files sent")
    (call,) = client.calls
    landscape, photo, portrait = call["file"]
    assert photo == str(tmp_path / "photo.png")
    _assert_video(landscape, (1920, 1080), LANDSCAPE.name)
    _assert_video(portrait, (1080, 1920), PORTRAIT.name)
    assert landscape.thumb is None and portrait.thumb is None
    assert client.uploads == [str(tmp_path / LANDSCAPE.name), str(tmp_path / PORTRAIT.name)]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_album_uploads_video_thumbnails_with_ffmpeg(client):
    await media.send_album("chat", [LANDSCAPE.name, PORTRAIT.name])

    (call,) = client.calls
    assert [item.thumb.name for item in call["file"]] == ["thumb.jpg", "thumb.jpg"]
    assert [type(upload) for upload in client.uploads] == [str, bytes, str, bytes]
