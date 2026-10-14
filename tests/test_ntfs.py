"""Native NTFS tests use binary fixtures; no elevated access is required."""

import struct

import pytest

from everything_mcp import ntfs


def usn_fixture(name, reference=42, parent=5, major=2, attributes=0):
    encoded = name.encode("utf-16-le")
    header_size = 60 if major == 2 else 76
    data = bytearray(header_size + len(encoded))
    struct.pack_into("<IHH", data, 0, len(data), major, 0)
    if major == 2:
        struct.pack_into("<QQ", data, 8, reference, parent)
        struct.pack_into("<IHH", data, 52, attributes, len(encoded), header_size)
    else:
        data[8:24] = reference.to_bytes(16, "little")
        data[24:40] = parent.to_bytes(16, "little")
        struct.pack_into("<IHH", data, 68, attributes, len(encoded), header_size)
    data[header_size:] = encoded
    return bytes(data)


@pytest.mark.parametrize("major", [2, 3])
def test_parse_variable_length_records_and_unicode(major):
    reference = (1 << 90) + 42 if major == 3 else 42
    data = usn_fixture("räksmörgås.txt", reference=reference, major=major)
    data += usn_fixture("folder", reference=43, major=major, attributes=0x10)
    records = list(ntfs.parse_usn_records(data))
    assert records == [
        ntfs.UsnRecord(reference, 5, "räksmörgås.txt", 0),
        ntfs.UsnRecord(43, 5, "folder", 0x10),
    ]


@pytest.mark.parametrize(
    "data",
    [
        b"\x00",
        struct.pack("<IHH", 0, 2, 0),
        struct.pack("<IHH", 80, 2, 0),
        struct.pack("<IHH", 8, 4, 0),
        struct.pack("<IHH", 8, 2, 0),
        usn_fixture("bad\\name"),
    ],
)
def test_malformed_records_fail_without_infinite_loop(data):
    with pytest.raises(ntfs.NtfsUnavailable):
        list(ntfs.parse_usn_records(data))


def test_reject_filename_bounds_and_odd_utf16_length():
    data = bytearray(usn_fixture("hello"))
    struct.pack_into("<H", data, 56, 3)
    with pytest.raises(ntfs.NtfsUnavailable, match="bounds"):
        list(ntfs.parse_usn_records(data))
    struct.pack_into("<HH", data, 56, 10, 10)
    with pytest.raises(ntfs.NtfsUnavailable, match="bounds"):
        list(ntfs.parse_usn_records(data))


def test_parent_resolution_handles_out_of_order_root_sequence_and_cycles():
    root_id = (4 << 48) | 5
    records = {
        31: ntfs.UsnRecord(31, 20, "report.pdf", 0),
        20: ntfs.UsnRecord(20, root_id, "Projects", 0x10),
        root_id: ntfs.UsnRecord(root_id, root_id, ".", 0x10),
        50: ntfs.UsnRecord(50, 51, "loop-a", 0x10),
        51: ntfs.UsnRecord(51, 50, "loop-b", 0x10),
        60: ntfs.UsnRecord(60, 9999, "orphan", 0),
    }
    entries = {item.path: item for item in ntfs.entries_from_records("D:\\", records)}
    assert set(entries) == {"D:\\Projects", "D:\\Projects\\report.pdf"}
    assert entries["D:\\Projects"].is_dir
    assert entries["D:\\Projects\\report.pdf"].size is None
    assert entries["D:\\Projects\\report.pdf"].date_modified is None


def test_deep_parent_chains_do_not_use_python_recursion():
    records = {5: ntfs.UsnRecord(5, 5, ".", 0x10)}
    for number in range(6, 1600):
        records[number] = ntfs.UsnRecord(number, number - 1, "d", 0x10)
    assert len(list(ntfs.entries_from_records("C:\\", records))) == 1594


@pytest.mark.parametrize("root", ["D:", "D:\\Projects", "\\\\server\\share\\", "/", "C:relative"])
def test_native_enumeration_requires_drive_root(root):
    with pytest.raises(ntfs.NtfsUnavailable, match="full drive root"):
        list(ntfs.iter_ntfs_entries(root))


def test_access_denied_is_available_for_caller_fallback(monkeypatch):
    def denied(root):
        raise ntfs.NtfsUnavailable(5, "Access is denied")

    monkeypatch.setattr(ntfs, "_read_volume_records", denied)
    with pytest.raises(ntfs.NtfsUnavailable) as error:
        list(ntfs.iter_ntfs_entries("D:\\"))
    assert error.value.errno == 5


def test_native_records_have_unknown_metadata(monkeypatch):
    records = {
        5: ntfs.UsnRecord(5, 5, ".", 0x10),
        6: ntfs.UsnRecord(6, 5, "hello.txt", 0),
    }
    monkeypatch.setattr(ntfs, "_read_volume_records", lambda root: records)
    assert list(ntfs.iter_ntfs_entries("d:/")) == [
        ntfs.NtfsEntry("D:\\hello.txt", "hello.txt", False),
    ]
