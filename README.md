# Noor Internet Speed Meter

Noor Internet Speed Meter is a FastAPI web app for checking internet download speed, upload speed, ping, and the selected test server. It replaces the old Tkinter desktop interface while keeping `internet_speedtest.py` in the repository for reference.

## Features
 
- FastAPI backend with a simple web interface
- Background speed tests so requests do not block
- Live progress polling from the browser
- Download, upload, ping, and server result cards
- Basic API tests that do not run a real speed test

## Project Structure

```text
app/
  main.py
  schemas.py
  services/
    speedtest_service.py
  templates/
    index.html
  static/
    css/styles.css
    js/app.js
tests/
  test_api.py
internet_speedtest.py
main.py
requirements.txt
README.md
.gitignore
```

## Installation

```bash
pip install -r requirements.txt
```

## Run The App

```bash
uvicorn app.main:app --reload
```

Or:

```bash
python main.py
```

Open:

```text
http://127.0.0.1:8000
```

## API Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/` | Main web page |
| GET | `/api/health` | Health check |
| POST | `/api/speedtest/start` | Start a background speed test |
| GET | `/api/speedtest/status/{test_id}` | Read speed test status |

## How It Works

The app creates a speed test job with a UUID, stores its status in memory, and runs the blocking `speedtest-cli` work in a thread. The browser polls the status endpoint once per second until the job completes or fails.

Progress values are updated at these points:

- 0 when created
- 10 when selecting a server
- 40 when testing download speed
- 75 when testing upload speed
- 100 when completed

## Testing

```bash
pytest
```

The tests mock the speed test function, so they do not use your network connection.

## Troubleshooting

- If `speedtest-cli` fails, check your internet connection and try again.
- If the app does not start, reinstall dependencies with `pip install -r requirements.txt`.
- If port `8000` is busy, run `uvicorn app.main:app --reload --port 8001`.

## Future Improvements

- Store speed test history
- Add a cancel button
- Add charts for repeated tests
