# Project Structure

```text
.
├── app.py                      Flask server, API routes, SQLite access, authentication
├── device.py                   RFID client, local cache, offline event queue, GPIO feedback
├── templates/
│   ├── login.html               Admin sign-in page
│   └── dashboard.html           Employee management and access-monitoring page
├── static/
│   ├── app.js                   Dashboard requests, employee actions, scan simulator, polling
│   └── style.css                Shared frontend styles
├── tests/
│   ├── test_app.py              Server routes, access decisions, authentication, and sync tests
│   └── test_device.py           Offline cache, access decisions, duration, and queue tests
├── data/
│   ├── sample_access_events.csv Demo records used by the Pandas notebook
│   └── local/                   Local SQLite test databases; not project source data
├── analysis.ipynb               Pandas analysis of access-event CSV data
├── requirements.txt             Flask and Pandas dependencies
├── requirements-device.txt      Optional Raspberry Pi GPIO/RFID dependencies
├── README.md                    Setup, run, and MVP limitation notes
└── .gitignore                   Excludes local environments, databases, and exported logs
```

`env/` is the local Python virtual environment. It is machine-specific and excluded from version control. Python bytecode/cache directories are generated automatically.

Run the app from the project root with `python app.py`. Run tests from the same directory with `env/bin/python -m unittest discover -v`.
