"""Optional, read-only NTFS filename enumeration, independent of Everything.

This is a bootstrap helper, not a complete MFT parser or durable USN reader.
USN records have no file sizes or file modification timestamps. Consumers must
stat paths when those fields are needed. Only one filename per file reference
is returned; additional hardlink names are not guaranteed. Directory walking
remains the portable, complete fallback. No journal is created or modified.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass

FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FILE_ATTRIBUTE_DIRECTORY = 0x10
_ROOT_RECORD = 5
_RECORD_NUMBER_MASK = (1 << 48) - 1


class NtfsUnavailable(OSError):
    """Native enumeration is unavailable; the caller should walk directories."""


@dataclass(frozen=True)
class UsnRecord:
    file_reference: int
    parent_reference: int
    name: str
    attributes: int


@dataclass(frozen=True)
class NtfsEntry:
    path: str
    name: str
    is_dir: bool
    size: int | None = None
    date_modified: float | None = None
    date_created: float | None = None


def parse_usn_records(data: bytes) -> Iterator[UsnRecord]:
    """Parse record bytes (without the enumeration buffer's 8-byte cursor).

    Reject malformed lengths and unsupported versions instead of publishing a
    silently incomplete index. V2 references are 64 bit; V3 references 128 bit.
    """
    position = 0
    while position < len(data):
        if len(data) - position < 8:
            raise NtfsUnavailable("Truncated USN record header")
        length, major, _minor = struct.unpack_from("<IHH", data, position)
        if length < 8 or length > len(data) - position:
            raise NtfsUnavailable("Invalid USN record length")
        record = data[position : position + length]
        if major == 2:
            minimum, attributes_offset, name_fields_offset = 60, 52, 56
            file_reference, parent_reference = (
                struct.unpack_from("<QQ", record, 8) if length >= minimum else (0, 0)
            )
        elif major == 3:
            minimum, attributes_offset, name_fields_offset = 76, 68, 72
            file_reference = int.from_bytes(record[8:24], "little")
            parent_reference = int.from_bytes(record[24:40], "little")
        else:
            raise NtfsUnavailable(f"Unsupported USN record version {major}")
        if length < minimum:
            raise NtfsUnavailable("Truncated USN record body")
        name_length, name_offset = struct.unpack_from("<HH", record, name_fields_offset)
        if name_offset < minimum or name_length % 2 or name_offset + name_length > length:
            raise NtfsUnavailable("Invalid USN filename bounds")
        try:
            name = record[name_offset : name_offset + name_length].decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise NtfsUnavailable("Invalid USN filename encoding") from exc
        if (
            not name
            or "\x00" in name
            or "/" in name
            or "\\" in name
            or name == ".."
            or (name == "." and file_reference & _RECORD_NUMBER_MASK != _ROOT_RECORD)
        ):
            raise NtfsUnavailable("Invalid USN filename")
        attributes = struct.unpack_from("<I", record, attributes_offset)[0]
        yield UsnRecord(file_reference, parent_reference, name, attributes)
        position += length


def _volume_root(root: str | os.PathLike[str]) -> str:
    candidate = os.fspath(root).replace("/", "\\")
    drive, tail = ntpath.splitdrive(candidate)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha() or tail != "\\":
        raise NtfsUnavailable("Native NTFS enumeration requires a full drive root, e.g. C:\\")
    return drive.upper() + "\\"


def _read_volume_records(root: str) -> dict[int, UsnRecord]:
    """Read MFT enumeration buffers using a short-lived volume handle."""
    if os.name != "nt":
        raise NtfsUnavailable("Native NTFS enumeration is only available on Windows")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW("\\\\.\\" + root[:2], 0x80000000, 0x7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise NtfsUnavailable(error, f"Cannot read NTFS volume {root}: {ctypes.FormatError(error)}")
    try:
        journal = ctypes.create_string_buffer(80)
        returned = wintypes.DWORD()
        if not kernel.DeviceIoControl(
            handle,
            FSCTL_QUERY_USN_JOURNAL,
            None,
            0,
            journal,
            len(journal),
            ctypes.byref(returned),
            None,
        ):
            error = ctypes.get_last_error()
            raise NtfsUnavailable(
                error, f"Cannot query existing NTFS journal: {ctypes.FormatError(error)}"
            )
        if returned.value < 24:
            raise NtfsUnavailable("Truncated USN journal information")
        high_usn = struct.unpack_from("<q", journal.raw, 16)[0]
        output = ctypes.create_string_buffer(1024 * 1024)
        cursor = 0
        records: dict[int, UsnRecord] = {}
        while True:
            request = ctypes.create_string_buffer(struct.pack("<Qqq", cursor, 0, high_usn))
            if not kernel.DeviceIoControl(
                handle,
                FSCTL_ENUM_USN_DATA,
                request,
                24,
                output,
                len(output),
                ctypes.byref(returned),
                None,
            ):
                error = ctypes.get_last_error()
                if error == 38:  # ERROR_HANDLE_EOF: enumeration is complete.
                    break
                raise NtfsUnavailable(
                    error, f"Cannot enumerate NTFS records: {ctypes.FormatError(error)}"
                )
            if returned.value < 8:
                raise NtfsUnavailable("Truncated NTFS enumeration buffer")
            next_cursor = struct.unpack_from("<Q", output.raw)[0]
            if next_cursor <= cursor:
                raise NtfsUnavailable("NTFS enumeration cursor did not advance")
            for record in parse_usn_records(output.raw[8 : returned.value]):
                records[record.file_reference] = record
            cursor = next_cursor
        return records
    finally:
        kernel.CloseHandle(handle)


def entries_from_records(root: str, records: dict[int, UsnRecord]) -> Iterator[NtfsEntry]:
    """Resolve parent graphs iteratively, skipping missing parents and cycles."""
    root = _volume_root(root)
    paths: dict[int, str | None] = {}
    for reference in records:
        if reference & _RECORD_NUMBER_MASK == _ROOT_RECORD:
            paths[reference] = root
    for reference, record in records.items():
        if reference in paths and paths[reference] == root:
            continue
        chain: list[UsnRecord] = []
        seen: set[int] = set()
        current = reference
        while current not in paths:
            if current in seen or current not in records:
                paths[current] = None
                break
            seen.add(current)
            parent_record = records[current]
            chain.append(parent_record)
            current = parent_record.parent_reference
        parent_path = paths.get(current)
        for child in reversed(chain):
            parent_path = ntpath.join(parent_path, child.name) if parent_path is not None else None
            paths[child.file_reference] = parent_path
        path = paths.get(reference)
        if path is not None:
            yield NtfsEntry(path, record.name, bool(record.attributes & FILE_ATTRIBUTE_DIRECTORY))


def iter_ntfs_entries(root: str | os.PathLike[str]) -> Iterator[NtfsEntry]:
    """Yield native filename records or raise NtfsUnavailable before yielding.

    The volume must already have a readable journal. Permission errors are
    propagated as NtfsUnavailable; this module never requests elevation.
    """
    volume = _volume_root(root)
    records = _read_volume_records(volume)
    yield from entries_from_records(volume, records)
