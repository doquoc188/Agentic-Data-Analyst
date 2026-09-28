# ReAct Agent

## Setup

Requires Python 3.10 or newer.

```powershell
conda activate llm
python -m pip install -r requirements.txt
```

If `.env` does not exist, copy `.env.example` to `.env`. Add your Gemini API key as `GOOGLE_API_KEY` in `.env`. The key is read from the environment when needed. Keep `.env` local; Git ignores it.

## Folder structure

```text
ReAct Agent/
├── app/
│   ├── __init__.py
│   ├── agent.py
│   ├── llm.py
│   └── tools.py
├── tests/
│   └── test_tools.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
