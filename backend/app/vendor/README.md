# Vendored third-party code

Copied in rather than installed, because AniFlow's image is built from this
repository alone: a path dependency would not exist inside the container, and
publishing a package to depend on it is not worth it for one library.

## anime_filename_parser

- Source: `anime-filename-parser` 0.2.3 (AniParse, the author's own library)
- Licence: MIT
- Vendored: 0.2.3 on 2026-10-13 (build 252, nine files replaced; the whole backend suite passed unchanged), 0.2.1 on 2026-09-17 (build 134), 0.1.0 on 2026-09-04
- Local changes: the CLI entry points (`cli.py`, `__main__.py`) were removed;
  AniFlow uses the library API only. Nothing else is modified, so the copy can
  be refreshed by replacing the folder.

### What 0.2.2 and 0.2.3 changed (build 252)

Taken as a whole rather than measured name by name: no existing AniFlow test changed its answer. Per AniParse's own
changelog, 0.2.3 reads Chinese fansub groups named `...字幕组` / `...字幕社` as the subgroup instead of a subtitle note, no
longer loses the words of an all-capitals title, and keeps the list of words that make a trailing number part of the
title short (a title ending in `Type 2` or `Class 3` keeps its season number). The version pin in
`test_a_number_in_the_title.py` follows the vendored version.

### What 0.2.1 changed for AniFlow

Measured over 127 real release names through
`services/release_parser.parse_release_name`, not by reading the library's own
notes: 22 names parse differently and 14 of those differences are in a field
AniFlow acts on.

- **A number in the title is no longer an episode number.** `The Villager of
  Level 999` was episode 999; it is now no episode at all. Every *real*
  release name of that show already parsed correctly in both versions - the
  number was only mistaken for an episode when the name carried no episode
  marker, such as a batch or a folder name. Those now raise
  `episode_not_identifiable` instead of quietly attaching to a phantom
  episode 999.
- **Scene release groups are recognised**: `...H 264-VARYG`,
  `...H.264-NanDesuKa`, `...-Kitsune` now set `subgroup` where it used to be
  `None` (11 of the 127). Also `HEVC-10bit` no longer reports the bit depth as
  the group, and a leading CJK bracket is read as a group.
- **Watch out for a profile pinned to "(no group)"** (the Unknown-group row):
  services/matching.py accepts a release *only* when it credits no group, so an
  anime pinned that way stops matching scene releases whose group is now read.
  Naming the group, or leaving the list empty, is the fix.
- **One regression found and fixed upstream before vendoring** - see 0.2.1 in
  AniParse's changelog. 0.2.0 read `[Group] OVA Special - 01.5` as episode
  **5**: anchoring the audio-layout protection (correctly, for
  `S22E31.1080p`) removed the protection a decimal episode relied on. AniFlow's
  own suite caught it; AniParse's 119 tests had no case for it.
- Unused additions: `tags` and `resolution`. AniFlow derives both itself
  (`services/release_edition.tags_in`, `_quality`), so the adapter ignores
  them rather than having two sources for one answer.

Why: AniFlow's own release-name parser left `S01E01 … WEB-DL DDP2.0 H` inside
the title and read `264` out of `H.264` as an episode number. Measured over 95
real release names, this library read 20 episode numbers AniFlow missed and got
5 right that AniFlow got wrong, with no runtime dependencies of its own.

Do not edit the vendored package to change AniFlow behaviour. The adapter in
`backend/app/services/release_parser.py` maps its output onto AniFlow's own
`ParsedRelease`, and that is where AniFlow-specific decisions belong.
