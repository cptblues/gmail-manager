"""Point d'entrée autonome pour les distributions PyInstaller de Gmail Manager."""
from __future__ import annotations

import socket
import sys
from pathlib import Path


def app_path() -> Path:
    # PyInstaller extrait les ressources dans _MEIPASS ; en développement elles
    # restent à côté de ce lanceur.
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "app.py"


def available_port(preferred: int = 8501) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    from streamlit.web import bootstrap

    script = app_path()
    if not script.is_file():
        raise FileNotFoundError(f"Interface introuvable : {script}")
    port = available_port()
    bootstrap.run(
        str(script),
        False,
        [],
        {
            "server.address": "127.0.0.1",
            "server.port": port,
            "server.headless": False,
            "browser.gatherUsageStats": False,
            "server.enableCORS": True,
            "server.enableXsrfProtection": True,
        },
    )


if __name__ == "__main__":
    main()
