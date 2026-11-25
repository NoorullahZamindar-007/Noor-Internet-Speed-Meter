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
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=reload)


if __name__ == "__main__":
    main()
