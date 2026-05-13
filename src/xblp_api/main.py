"""Uvicorn entrypoint — run with: python -m xblp_api"""

import uvicorn

from xblp_api.app import create_app
from xblp_api.config import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
