# DaVinci Resolve Studio CLI harness

This harness controls a locally installed, running DaVinci Resolve Studio
desktop session through the version-matched scripting bridge that ships with
Resolve. It is not a cloud API client and does not require an API key.

The application remains the source of truth. Mutating commands are saved back
to the current project and support `--dry-run` where planning is meaningful.

See `cli_anything/davinci_resolve/README.md` for command details.
