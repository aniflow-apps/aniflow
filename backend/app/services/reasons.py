"""The vocabulary of blocked/skipped reasons the UI has to be able to name.

Every value that can land in ``TorrentDownload.attention_reason`` lives here -
plus ``episode_not_identifiable`` (build 168), which never gets that far:
queueing raises it straight onto the ``Attention`` record, because there is no
download row yet to carry it. Same vocabulary, same renderer
(``reasonText()``), so it lives in the same list rather than starting a second
one for an audience of one.

The frontend renders it as ``import.reason.<value>``, and a missing key shows
the raw identifier to the user - which is exactly what happened with
``duplicate_episode``. ``test_reason_vocabulary`` compares this tuple against
both locale files, so adding a reason without a translation fails the suite.
"""

#: Set by the queueing pass, before anything is handed to the torrent client.
QUEUE_REASONS = (
    "duplicate_episode",
    "superseded_version",
    "new_version_needs_review",
    "qb_not_configured",
    # Two rows, one torrent (build 118). The row that knows more about the
    # download keeps it; this is what the other one is left saying, rather
    # than being deleted while somebody is looking at the page.
    "duplicate_of_another_row",
    # Not a download reason at all - see the module docstring. A MATCHED
    # release whose episode number could not be read from its name, most
    # often a batch. Written straight onto an EPISODE_NOT_IDENTIFIABLE
    # Attention, since there is nothing to download yet.
    "episode_not_identifiable",
)

#: Set by the sync pass, from what the client reports.
SYNC_REASONS = ("missing_in_client",)

#: Set by the importer when a finished download cannot be placed.
IMPORT_REASONS = (
    "media_folder_missing",
    "path_outside_root",
    "source_unknown",
    # The download's file is not where it was recorded: deleted by hand, or
    # moved by something other than AniFlow. Retried, because a client that is
    # briefly unreachable produces this too and recovers on its own.
    "source_missing",
    "target_exists",
    "no_media_found",
    "ambiguous_multi_video",
    "anime_missing",
    # The library folder cannot be written to (build 231): not writable by the user AniFlow runs as, or on a
    # read-only share. The Libraries page already says "read-only"; this is the download that ran into it.
    "library_not_writable",
    # Anything else that stopped the file being placed: a full disk, a share that went away.
    "library_write_failed",
    # Placing by hardlink (build 272): the download folder and the library are on different mounts or filesystems
    # - the same disk bind-mounted twice counts - so no link can join them. The Checks page names the fix.
    "hardlink_cross_device",
    # The filesystem refused to make a link (some network shares, FAT/exFAT, a protected-hardlinks rule).
    "hardlink_unsupported",
    # Shoko Server mode: the import volume is not mounted, so there is nowhere
    # to hand a file over to.
    "import_root_missing",
    # Shoko Server mode: the file was handed over and is still sitting in the
    # import folder long afterwards. Not an AniFlow failure - the file is
    # exactly where it was put - so it reads as "go and look at Shoko".
    "shoko_not_imported",
    # The file is downloaded and in the download folder, the library has
    # nothing for that episode, and nothing is moving it: the torrent left the
    # client before AniFlow noticed it had finished. Raised by the reconcile
    # pass for an anime with Automate off - with it on, the file is placed
    # instead of reported. See services/reconcile.py.
    "ready_to_move",
    # Shoko Server mode: the file left the import folder and never appeared in
    # the library. Shoko took it and did something else with it - most often
    # it decided the collection already had this episode.
    "shoko_file_gone",
)

ALL_REASONS = QUEUE_REASONS + SYNC_REASONS + IMPORT_REASONS

__all__ = ["ALL_REASONS", "IMPORT_REASONS", "QUEUE_REASONS", "SYNC_REASONS"]
