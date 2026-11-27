import os

import uvicorn


def main() -> None:
    # The auto-reloader is a development aid: running `python main.py`
    # used to enable it unconditionally, so any file change restarted
    # the server (and its in-flight tests). A plain run now serves
    # stably; opt back in with NOOR_SPEEDMETER_RELOAD=1 when developing.
    reload = os.getenv("NOOR_SPEEDMETER_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    host = os.getenv("NOOR_SPEEDMETER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    # A non-numeric or out-of-range port falls back to 8000 instead of
    # crashing startup: int() accepts "99999" and "0" happily, but
    # uvicorn raises on a port outside 1-65535.
    try:
        port = int(os.getenv("NOOR_SPEEDMETER_PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("port out of range")
    except ValueError:
        port = 8000
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
