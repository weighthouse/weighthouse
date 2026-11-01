"""Filenames and partial-file suffixes of the durable library state."""

LIBRARY_DB_FILENAME = "library.sqlite3"
LEGACY_DB_FILENAME = "modelharbor.sqlite3"

MANIFEST_FILENAME = ".modelmanifest.json"
LEGACY_MANIFEST_FILENAME = ".hugginghack.json"

# Transient partial files: chunked uploads and S3 restore.
PART_SUFFIX = ".weighthouse-part"
LEGACY_PART_SUFFIXES = (".modelharbor-part", ".hugginghack-part", ".hugginghack-s3-part")
