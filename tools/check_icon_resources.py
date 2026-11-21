"""Verify that Windows executables contain the Gilhound icon from assets/gilhound.ico."""

import argparse
import ctypes
import struct
from collections import Counter
from ctypes import wintypes
from pathlib import Path

import pefile

SOURCE = Path(__file__).resolve().parents[1] / "assets" / "gilhound.ico"


def icon_frames() -> Counter[bytes]:
    data = SOURCE.read_bytes()
    count = struct.unpack_from("<H", data, 4)[0]
    frames = []
    for index in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + index * 16 + 8)
        frames.append(data[offset:offset + size])
    return Counter(frames)


def executable_frames(path: Path) -> Counter[bytes]:
    with pefile.PE(str(path)) as exe:
        icon_type = pefile.RESOURCE_TYPE["RT_ICON"]
        return Counter(
            exe.get_data(entry.data.struct.OffsetToData, entry.data.struct.Size)
            for resource in exe.DIRECTORY_ENTRY_RESOURCE.entries
            if resource.id == icon_type
            for icon in resource.directory.entries
            for entry in icon.directory.entries
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executables", nargs="+", type=Path)
    args = parser.parse_args()
    extract_icon = ctypes.windll.shell32.ExtractIconExW
    extract_icon.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ]
    extract_icon.restype = wintypes.UINT

    expected = icon_frames()
    for path in args.executables:
        count = extract_icon(str(path.resolve(strict=True)), -1, None, None, 0)
        if count in (0, wintypes.UINT(-1).value) or expected - executable_frames(path):
            raise SystemExit(f"{path.name}: Gilhound icon resources are missing or incomplete.")
        print(f"{path.name}: {count} icon group(s), all {expected.total()} Gilhound frames found.")


if __name__ == "__main__":
    main()
