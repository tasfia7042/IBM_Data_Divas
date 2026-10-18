"""DocTest web UI. Run: python ui/app.py  (binds 0.0.0.0, port from $PORT, default 5000).
Shows Phase 1 (documents -> scenarios) and Phase 2 (scenarios -> all_tests.json). Never runs SQL."""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, render_template, request  # noqa: E402

import adapters  # noqa: E402
from jobs import JobError, Jobs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("doctest-ui")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # whole request; each file is checked against 20 MB
jobs = Jobs()
_lock = threading.Lock()
_current = {"run": adapters.Run.latest()}


def run() -> adapters.Run:
    with _lock:
        return _current["run"]


def set_run(r: adapters.Run) -> None:
    with _lock:
        _current["run"] = r


def busy():
    return (jsonify(error="Another job is still running. Wait for it to finish."), 409) if jobs.running() else None


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="Upload is too large."), 413


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def state():
    r = run()
    return jsonify(run=r.id, demo=bool(r.meta().get("demo")), files=r.files(), scenarios=adapters.scenario_cards(r),
                   tests=adapters.tests_for(r), job=jobs.running())


@app.get("/api/scenarios")
def scenarios():
    return jsonify(adapters.scenario_cards(run()))


@app.get("/api/tests")
def tests():
    """Reads the Phase 2 file fresh on every call. ?reload=1 leaves demo mode and reads TESTS_PATH."""
    r = run()
    if request.args.get("reload") and r.meta().get("tests_path"):
        meta = r.meta()
        meta.pop("tests_path", None)
        r.write("meta.json", meta)
    return jsonify(adapters.tests_for(r))


@app.post("/api/upload")
def upload():
    if (b := busy()):
        return b
    errors, saved = [], []
    for f in request.files.getlist("files"):
        try:
            saved.append(adapters.save_upload(run(), f.filename or "file", f.read()))
        except JobError as e:
            errors.append(str(e))
    return jsonify(files=run().files(), saved=saved, errors=errors), (200 if saved or not errors else 400)


@app.delete("/api/files/<path:name>")
def delete_file(name):
    if (b := busy()):
        return b
    p = run().files_dir / adapters.safe_name(name)
    if p.exists():
        p.unlink()
    return jsonify(files=run().files())


def _start(kind: str, fn):
    try:
        return jsonify(job_id=jobs.start(kind, fn))
    except JobError as e:
        return jsonify(error=str(e)), 409


@app.post("/api/phase1")
def phase1():
    r = run()
    if not r.files():
        return jsonify(error="Upload at least one file first."), 400
    if jobs.running():
        return jsonify(error="Another job is still running. Wait for it to finish."), 409
    r.clear("scenarios.json", "scenarios_view.json")  # clears column 2; the test file is left alone
    return _start("phase1", lambda p: adapters.run_phase1(r, p))


@app.post("/api/phase2")
def phase2():
    r = run()
    if not r.read("scenarios.json"):
        return jsonify(error="There are no scenarios yet. Run Phase 1 first."), 400
    return _start("phase2", lambda p: adapters.run_phase2(r, p))


@app.get("/api/jobs/<job_id>")
def job(job_id):
    j = jobs.get(job_id)
    return jsonify(j) if j else (jsonify(error="Unknown job."), 404)


@app.post("/api/demo")
def demo():
    if (b := busy()):
        return b
    set_run(adapters.load_demo())
    return state()


@app.post("/api/reset")
def reset():
    """Clears uploads and scenarios (a new empty run). Does not delete all_tests.json."""
    if (b := busy()):
        return b
    set_run(adapters.Run.new())
    return state()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    log.info("DocTest UI on http://0.0.0.0:%s (run %s, tests file %s)", port, run().id, adapters.tests_path())
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
