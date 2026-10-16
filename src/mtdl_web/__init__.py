"""mtdl-web: search, download and subscribe to the German public broadcasters' media libraries."""

__version__ = "0.1.0.dev35"

PROJECT_URL = "https://github.com/sobrino89/mtdl-web"

# Sent with every outgoing request (film list, MediathekViewWeb, downloads) so operators can reach us.
USER_AGENT = f"mtdl-web/{__version__} (+{PROJECT_URL})"
