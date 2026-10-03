# ReAct Agent

## Setup

Requires Python 3.10 or newer.

```powershell
conda activate llm
python -m pip install -r requirements.txt
```

Create a local `.env` if needed. Set `GOOGLE_API_KEY` and the database variables
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`. Use the read-only
`analyst_agent` database role. Keep `.env` local; Git ignores it.

## Run observability

Agent runs now save local structured JSON traces under Git-ignored `runs/`.
Inspect a saved run without calling Gemini:

```powershell
python -m app.trace --latest
python -m app.trace "D:\ReAct Agent\runs\<trace-file>.json"
```

See [the tracing guide](docs/tracing.md) for the schema, preview limits,
redaction, failure handling, and evaluation linkage.

## Folder structure

```text
ReAct Agent/
├── app/
│   ├── __init__.py
│   ├── agent.py
│   ├── llm.py
│   ├── database.py
│   ├── tools.py
│   └── trace.py
├── tests/
│   ├── test_agent.py
│   ├── test_tools.py
│   ├── test_eval_cases.py
│   ├── test_eval_runner.py
│   └── test_trace.py
├── docs/tracing.md
├── eval/
├── sql/
├── runs/                 # Local generated traces; ignored by Git
├── .gitignore
├── requirements.txt
└── README.md
```
