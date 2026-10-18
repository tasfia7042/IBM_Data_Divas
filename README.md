# How to run

Run everything from this folder (`IBM_data_diva/`). Needs Python 3.10+ and [Ollama](https://ollama.com).

## 1. Install (one time)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ollama pull qwen2.5:7b-instruct
```

## 2. Start Ollama

In a separate terminal:

```bash
ollama serve
```

Skip this if Ollama is already running.

## 3. Start the UI

```bash
PORT=5050 .venv/bin/python ui/app.py
```

Open **http://localhost:5050**. Stop it with `Ctrl+C`.

## 4. Use it

1. Upload files (for example the ones in `test/`) and click **Run Phase 1**.
2. Scenarios appear in the middle column.
3. Click **Reload file** to show the test cases from `DBMS_testgen/results/all_tests.json`.

To try it without Ollama, click **Load demo**.
