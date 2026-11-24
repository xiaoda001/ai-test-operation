"""Run the FastAPI development server."""

import os

import uvicorn


def main() -> None:
    """Start the backend on its configured local development port."""
    port = int(os.getenv("BACKEND_PORT", "10110"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
