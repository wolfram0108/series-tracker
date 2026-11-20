"""The alignment daemon may run on Windows: the same storage is named in its style, and a path crosses the boundary in both
directions as the same location, whatever separators and letter case the daemon writes it with."""
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from modules.conform.paths import folder, is_absolute, is_root, name_of, reverse, translate

HERE = "/nas/torrents/track-muxer/series-tracker"
POSIX_ROOT = "/mnt/meganas/media/torrents/track-muxer/series-tracker"
# How the owner may name that folder at a Windows daemon: a mapped drive in either separator, a share.
WINDOWS_ROOTS = ["Z:\\torrents\\track-muxer\\series-tracker", "Z:/torrents/track-muxer/series-tracker",
                 "\\\\nas\\media\\torrents\\track-muxer\\series-tracker"]
# Spaces, a colon and Cyrillic are what the folders of a job carry.
INSIDE = ["downloads", "downloads/7/s01e01/a.mkv", "conform/planner/7/plan:7:1 #k/_aligned/озвучка 1.mka"]


@pytest.mark.parametrize("inside", INSIDE)
def test_a_path_goes_to_a_posix_daemon_as_the_same_location(inside):
    sent = translate(f"{HERE}/{inside}", {HERE: POSIX_ROOT})
    assert PurePosixPath(sent) == PurePosixPath(POSIX_ROOT).joinpath(*inside.split("/"))
    assert translate(sent, reverse({HERE: POSIX_ROOT})) == f"{HERE}/{inside}"


@pytest.mark.parametrize("root", WINDOWS_ROOTS)
@pytest.mark.parametrize("inside", INSIDE)
def test_a_path_goes_to_a_windows_daemon_as_the_same_location_in_the_way_the_owner_named_its_folder(root, inside):
    sent = translate(f"{HERE}/{inside}", {HERE: root})
    assert PureWindowsPath(sent) == PureWindowsPath(root).joinpath(*inside.split("/"))
    other = "/" if "\\" in root else "\\"
    assert other not in sent


@pytest.mark.parametrize("root", WINDOWS_ROOTS)
@pytest.mark.parametrize("inside", INSIDE)
def test_whatever_way_a_windows_daemon_writes_a_path_it_comes_back_as_the_same_local_location(root, inside):
    sent = translate(f"{HERE}/{inside}", {HERE: root})
    # The other separators, and the letter case of the drive and the folders (the file keeps its own case).
    swapped = root.swapcase() + sent[len(root):]
    for written in (sent, sent.replace("\\", "/"), sent.replace("/", "\\"), swapped, swapped.replace("\\", "/")):
        assert translate(written, reverse({HERE: root})) == f"{HERE}/{inside}", written


@pytest.mark.parametrize("root", [POSIX_ROOT, *WINDOWS_ROOTS])
def test_a_path_outside_the_shared_folder_is_never_mapped_in_either_direction(root):
    for outside in (root + "2/a", "/home/x/a", "Y:\\torrents\\a", "Z:torrents\\a"):
        with pytest.raises(ValueError):
            translate(outside, reverse({HERE: root}))
    for outside in (f"{HERE}2/a", "/home/x/a", "relative/a"):
        with pytest.raises(ValueError):
            translate(outside, {HERE: root})


@pytest.mark.parametrize("path, absolute", [
    ("/a", True), ("Z:\\a", True), ("Z:/a", True), ("\\\\srv\\share\\a", True), ("\\\\srv\\share\\", True),
    ("a/b", False), ("a\\b", False), ("Z:a", False), ("Z:", False), ("\\a", False), ("", False)])
def test_a_path_is_complete_in_its_own_style(path, absolute):
    assert is_absolute(path) is absolute


@pytest.mark.parametrize("path, top", [("/", True), ("Z:\\", True), ("Z:/", True), ("\\\\srv\\share\\", True),
                                       ("/a", False), ("Z:\\a", False), ("\\\\srv\\share\\a", False)])
def test_the_top_of_a_storage_is_the_top_in_every_style(path, top):
    assert is_root(path) is top


@pytest.mark.parametrize("typed, stored", [
    (" /m/w/ ", "/m/w"), ("/", "/"), ("", ""), (" Z:\\torrents\\x\\ ", "Z:\\torrents\\x"), ("Z:/torrents/x//", "Z:/torrents/x"),
    ("Z:\\", "Z:\\"), ("Z:/", "Z:/"), ("\\\\nas\\media", "\\\\nas\\media\\"), ("\\\\nas\\media\\x\\", "\\\\nas\\media\\x")])
def test_a_folder_is_stored_without_trailing_separators_and_the_top_of_a_storage_stays_whole(typed, stored):
    assert folder(typed) == stored


@pytest.mark.parametrize("path, name", [("/mnt/x/a.mp4", "a.mp4"), ("Z:\\x\\a.mp4", "a.mp4"), ("Z:/x/a.mp4", "a.mp4"),
                                        ("\\\\nas\\media\\x\\a.mp4", "a.mp4"), ("a.mp4", "a.mp4"), ("", "")])
def test_the_file_name_of_a_path_of_the_daemon_is_read_in_its_style(path, name):
    assert name_of(path) == name
