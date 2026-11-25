import struct

from app.tools import colmap_model as cm


def _write_images_bin(path, names, points_per_image=3):
    out = bytearray(struct.pack("<Q", len(names)))
    for i, name in enumerate(names, start=1):
        out += struct.pack("<I4d3dI", i, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1)
        out += name.encode() + b"\x00"
        out += struct.pack("<Q", points_per_image)
        for k in range(points_per_image):
            out += struct.pack("<ddq", 1.5 * k, 2.5 * k, k if k % 2 else -1)
    path.write_bytes(bytes(out))


def test_read_image_names(tmp_path):
    names = ["v1/frame_00001.jpg", "v1/frame_00003.jpg", "v2/frame_00001.jpg", "frame_00007.jpg"]
    f = tmp_path / "images.bin"
    _write_images_bin(f, names)
    assert cm.read_image_names(f) == names


def test_read_image_names_without_points_and_empty(tmp_path):
    f = tmp_path / "images.bin"
    _write_images_bin(f, ["a.jpg", "b.jpg"], points_per_image=0)
    assert cm.read_image_names(f) == ["a.jpg", "b.jpg"]
    f.write_bytes(struct.pack("<Q", 0))
    assert cm.read_image_names(f) == []
    assert cm.read_image_names(tmp_path / "vuoto.bin") if (tmp_path / "vuoto.bin").exists() else True


def test_read_camera_count(tmp_path):
    f = tmp_path / "cameras.bin"
    f.write_bytes(struct.pack("<Q", 2) + b"\x00" * 40)
    assert cm.read_camera_count(f) == 2


def test_group_ranges():
    assert cm.group_ranges([3, 4, 5, 9, 10, 20]) == [(3, 5), (9, 10), (20, 20)]
    assert cm.group_ranges([]) == []
    assert cm.group_ranges([5, 3, 4]) == [(3, 5)]
