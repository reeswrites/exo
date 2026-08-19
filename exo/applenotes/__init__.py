"""applenotes — vendored Apple Notes NoteStore.sqlite decoder (copied from second-brain
cli/applenotes on 2026-08-09 so Exo OWNS the decode, no cross-repo dep). Stdlib
only: gzip + sqlite3 + manual protobuf varint decode. Only `extract` is used here."""
