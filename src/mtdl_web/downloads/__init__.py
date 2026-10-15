"""Downloads: every byte through one bandwidth limit; mp4 over HTTP with resume, HLS segments fetched by us and
muxed by ffmpeg locally (ffmpeg never touches the network), subtitles converted to SRT."""


class DownloadError(Exception):
    """A download failed for a reason worth showing to the user; ``retryable`` if trying again later may help.

    ``status`` is the HTTP status if the server answered, ``code`` the kind of failure otherwise ("network",
    "format", "livestream", "no_url", "mux"); the manager turns both into the error code the UI explains."""

    def __init__(
        self, message: str, *, retryable: bool = False, status: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code
