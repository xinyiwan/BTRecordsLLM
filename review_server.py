"""Stdlib HTTP server for manually reviewing BTRecordsLLM predictions.

Builds on server.py's CSV/JSON parsing (imported, not duplicated) and adds a
per-field correct/incorrect checkbox review workflow, with the verdicts
persisted to a JSON sidecar file so review progress survives restarts.

Run:
    python3 review_server.py                     # serves on http://127.0.0.1:8001
    python3 review_server.py -p 9001
    python3 review_server.py -f path/to/output.csv
    python3 review_server.py -f path/to/output.csv -r path/to/reviews.json

For each row, the page shows the extracted fields from final_output as a
table with a Correct/Incorrect toggle per field. Verdicts are saved
immediately on click. The sidebar shows per-row progress (n/total fields
reviewed) and the top of the page shows overall stats: total reports,
reports with at least one reviewed field, reports fully reviewed, and
correct/reviewed counts (overall and per field).
"""

import argparse
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from server import DEFAULT_CSV, META_COLS, load_csv, parse_final_output, patient_summary

ROWS: list[dict] = []
REVIEWS: dict[str, dict[str, bool]] = {}
REVIEWS_PATH: Path = None  # type: ignore[assignment]
REVIEWS_LOCK = threading.Lock()


def load_reviews(path: Path) -> dict[str, dict[str, bool]]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_reviews_locked() -> None:
    """Caller must hold REVIEWS_LOCK."""
    tmp = REVIEWS_PATH.with_suffix(REVIEWS_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(REVIEWS, f, indent=2, sort_keys=True)
    tmp.replace(REVIEWS_PATH)


def fields_for_row(idx: int) -> list[str]:
    parsed = parse_final_output(ROWS[idx].get("final_output", ""))
    if not isinstance(parsed, dict):
        return []
    return list(parsed.keys())


def row_progress(idx: int) -> dict:
    total = len(fields_for_row(idx))
    review = REVIEWS.get(str(idx), {})
    reviewed = len(review)
    correct = sum(1 for v in review.values() if v is True)
    return {"total": total, "reviewed": reviewed, "correct": correct}


def compute_stats() -> dict:
    total_reports = len(ROWS)
    reports_started = 0
    reports_complete = 0
    total_fields = 0
    total_reviewed = 0
    total_correct = 0
    per_field: dict[str, dict[str, int]] = {}

    for idx in range(total_reports):
        fields = fields_for_row(idx)
        review = REVIEWS.get(str(idx), {})
        total_fields += len(fields)
        if review:
            reports_started += 1
        if fields and all(f in review for f in fields):
            reports_complete += 1
        for f in fields:
            stat = per_field.setdefault(f, {"reviewed": 0, "correct": 0, "total": 0})
            stat["total"] += 1
            if f in review:
                total_reviewed += 1
                stat["reviewed"] += 1
                if review[f] is True:
                    total_correct += 1
                    stat["correct"] += 1

    return {
        "total_reports": total_reports,
        "reports_started": reports_started,
        "reports_complete": reports_complete,
        "total_fields": total_fields,
        "total_reviewed": total_reviewed,
        "total_correct": total_correct,
        "per_field": per_field,
    }


def patient_detail(row: dict, idx: int) -> dict:
    final_parsed = parse_final_output(row.get("final_output", ""))
    meta = {c: row.get(c, "") for c in META_COLS}
    return {
        "idx": idx,
        "meta": meta,
        "valoracion": row.get("valoracion", ""),
        "valoracion_en": row.get("valoracion_en_clean", "") or row.get("valoracion_en", ""),
        "translation_thinking": row.get("valoracion_thinking", ""),
        "final_output_raw": row.get("final_output", ""),
        "final_output": final_parsed,
        "reasoning": row.get("reasoning", ""),
        "review": REVIEWS.get(str(idx), {}),
        "progress": row_progress(idx),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BTRecords Review</title>
<style>
  :root {
    --bg: #f7f7f8;
    --panel: #ffffff;
    --border: #e2e2e6;
    --muted: #6b7280;
    --accent: #2563eb;
    --accent-soft: #dbeafe;
    --good: #16a34a;
    --good-soft: #dcfce7;
    --bad: #dc2626;
    --bad-soft: #fee2e2;
    --code-bg: #0f172a;
    --code-fg: #e2e8f0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: #111827; background: var(--bg);
    height: 100vh; display: flex;
  }
  aside {
    width: 320px; flex-shrink: 0; background: var(--panel); border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
  }
  aside header { padding: 14px 16px; border-bottom: 1px solid var(--border); }
  aside header h1 { margin: 0; font-size: 16px; }
  aside header .count { color: var(--muted); font-size: 12px; }
  #filter {
    margin: 10px 14px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 13px;
  }
  #patients { flex: 1; overflow-y: auto; padding: 0 6px 12px; }
  .patient {
    padding: 8px 10px; margin: 2px 0; border-radius: 6px; cursor: pointer; font-size: 13px;
    border: 1px solid transparent;
  }
  .patient:hover { background: #f1f5f9; }
  .patient.active { background: var(--accent-soft); border-color: var(--accent); }
  .patient .pid { font-weight: 600; display: flex; justify-content: space-between; gap: 6px; }
  .patient .sub { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .progress-pill {
    font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #f1f5f9; color: var(--muted);
  }
  .progress-pill.complete { background: var(--good-soft); color: var(--good); }
  .progress-pill.started { background: var(--accent-soft); color: var(--accent); }

  main { flex: 1; overflow-y: auto; padding: 22px 28px; }
  main .empty { color: var(--muted); margin-top: 80px; text-align: center; }

  .stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 8px 18px; padding: 14px 16px; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px;
  }
  .stats div span { color: var(--muted); font-size: 11px; display: block; text-transform: uppercase; letter-spacing: .04em; }
  .stats div b { font-size: 18px; font-weight: 700; }

  .meta {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 8px 18px; padding: 14px 16px; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px;
  }
  .meta div span { color: var(--muted); font-size: 11px; display: block; text-transform: uppercase; letter-spacing: .04em; }
  .meta div b { font-size: 13px; font-weight: 600; }

  details {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    margin-bottom: 14px; overflow: hidden;
  }
  details > summary {
    padding: 12px 16px; cursor: pointer; font-weight: 600; user-select: none;
    list-style: none; display: flex; align-items: center; gap: 8px;
  }
  details > summary::-webkit-details-marker { display: none; }
  details > summary::before {
    content: "▶"; font-size: 10px; color: var(--muted); transition: transform .15s;
  }
  details[open] > summary::before { transform: rotate(90deg); }
  details > .body { padding: 0 16px 16px; }

  pre.report {
    white-space: pre-wrap; word-wrap: break-word; font-family: ui-monospace, Menlo, monospace;
    background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; font-size: 13px; line-height: 1.5; margin: 0;
  }
  table.review { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.review th, table.review td { text-align: left; padding: 8px 10px; border-bottom: 1px dashed var(--border); vertical-align: top; }
  table.review th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  table.review .field-name { font-weight: 600; color: #1f2937; width: 220px; }
  table.review .field-value { white-space: pre-wrap; }
  table.review .na { color: var(--muted); font-style: italic; }
  .verdict-btns { display: flex; gap: 6px; white-space: nowrap; }
  .verdict-btn {
    border: 1px solid var(--border); background: var(--panel); border-radius: 6px;
    padding: 4px 10px; font-size: 12px; cursor: pointer; color: var(--muted);
  }
  .verdict-btn.correct.active { background: var(--good-soft); color: var(--good); border-color: var(--good); }
  .verdict-btn.incorrect.active { background: var(--bad-soft); color: var(--bad); border-color: var(--bad); }
  .badge {
    display: inline-block; background: var(--accent-soft); color: var(--accent);
    padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-right: 4px;
  }
  pre.code {
    background: var(--code-bg); color: var(--code-fg); border-radius: 6px; padding: 12px;
    font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word;
  }
  .section-title { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin: 18px 0 6px; }
</style>
</head>
<body>
<aside>
  <header>
    <h1>BTRecords Review</h1>
    <div class="count" id="count"></div>
  </header>
  <input id="filter" placeholder="Filter by id, modality, date..." />
  <div id="patients"></div>
</aside>
<main id="main">
  <div class="empty">Select a patient on the left.</div>
</main>
<script>
const fmt = (v) => v == null || v === "" ? "—" : v;
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let patients = [];
let activeIdx = null;

async function loadStats() {
  const res = await fetch("/api/stats");
  return res.json();
}

async function loadPatients() {
  const res = await fetch("/api/patients");
  patients = await res.json();
  document.getElementById("count").textContent = patients.length + " reports";
  renderList("");
}

function progressPill(p) {
  if (!p.total) return `<span class="progress-pill">no fields</span>`;
  const cls = p.reviewed >= p.total ? "complete" : (p.reviewed > 0 ? "started" : "");
  return `<span class="progress-pill ${cls}">${p.reviewed}/${p.total}</span>`;
}

function renderList(q) {
  const list = document.getElementById("patients");
  const ql = q.trim().toLowerCase();
  const filtered = !ql ? patients : patients.filter(p =>
    [p.info_key, p.sip, p.date, p.modality, p.prestacion].some(v => String(v ?? "").toLowerCase().includes(ql))
  );
  list.innerHTML = filtered.map(p => `
    <div class="patient ${p.idx === activeIdx ? "active" : ""}" data-idx="${p.idx}">
      <div class="pid">
        <span>#${escapeHtml(p.info_key)} <span style="color:var(--muted);font-weight:400">· sip ${escapeHtml(p.sip)}</span></span>
        ${progressPill(p.progress)}
      </div>
      <div class="sub">${escapeHtml(p.date)} · ${escapeHtml(p.modality)}</div>
      <div class="sub">${escapeHtml(p.prestacion)}</div>
    </div>
  `).join("");
  list.querySelectorAll(".patient").forEach(el =>
    el.addEventListener("click", () => selectPatient(parseInt(el.dataset.idx, 10)))
  );
}

document.getElementById("filter").addEventListener("input", e => renderList(e.target.value));

async function selectPatient(idx) {
  activeIdx = idx;
  renderList(document.getElementById("filter").value);
  const res = await fetch("/api/patient/" + idx);
  const data = await res.json();
  renderDetail(data);
}

async function refreshSidebarProgress(idx, progress) {
  const p = patients.find(x => x.idx === idx);
  if (p) p.progress = progress;
  renderList(document.getElementById("filter").value);
}

async function setVerdict(idx, field, correct) {
  const res = await fetch(`/api/review/${idx}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({field, correct}),
  });
  const data = await res.json();
  refreshSidebarProgress(idx, data.progress);
  updateStatsPanel();
  return data;
}

function renderReviewTable(idx, obj, review) {
  if (!obj || typeof obj !== "object") return '<div class="na">Could not parse final_output as JSON.</div>';
  const rows = Object.entries(obj).map(([k, v]) => {
    let rendered;
    if (Array.isArray(v)) {
      rendered = v.length ? v.map(x => `<span class="badge">${escapeHtml(x)}</span>`).join("") : '<span class="na">[]</span>';
    } else if (v === null || v === "" || v === "Not specified") {
      rendered = `<span class="na">${escapeHtml(v ?? "")}</span>`;
    } else if (typeof v === "object") {
      rendered = `<pre class="code">${escapeHtml(JSON.stringify(v, null, 2))}</pre>`;
    } else {
      rendered = escapeHtml(v);
    }
    const verdict = review[k]; // true, false, or undefined
    const fieldKey = escapeHtml(k).replace(/"/g, "&quot;");
    return `
      <tr data-field="${fieldKey}">
        <td class="field-name">${escapeHtml(k)}</td>
        <td class="field-value">${rendered}</td>
        <td>
          <div class="verdict-btns">
            <button class="verdict-btn correct ${verdict === true ? "active" : ""}" data-verdict="true">Correct</button>
            <button class="verdict-btn incorrect ${verdict === false ? "active" : ""}" data-verdict="false">Incorrect</button>
          </div>
        </td>
      </tr>`;
  }).join("");
  return `
    <table class="review">
      <thead><tr><th>Field</th><th>Predicted value</th><th>Review</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function attachVerdictHandlers(idx) {
  document.querySelectorAll("table.review tbody tr").forEach(tr => {
    const field = tr.dataset.field;
    tr.querySelectorAll(".verdict-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const correct = btn.dataset.verdict === "true";
        await setVerdict(idx, field, correct);
        tr.querySelectorAll(".verdict-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
    });
  });
}

function renderDetail(d) {
  const m = d.meta || {};
  const metaCells = [
    ["info_key", m.info_key], ["sip", m.sip], ["birth", m.fechaNaci],
    ["date", m.fechaHoraRealizacion], ["modality", m.modalidad],
    ["prestacion", m.prestacionCentro], ["cancer", m.cancer], ["timepoint", m.timepoint],
  ].map(([k, v]) => `<div><span>${k}</span><b>${escapeHtml(fmt(v))}</b></div>`).join("");

  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="meta">${metaCells}</div>

    <details>
      <summary>Original report (Spanish)</summary>
      <div class="body"><pre class="report">${escapeHtml(d.valoracion)}</pre></div>
    </details>

    <details open>
      <summary>Translated report (English)</summary>
      <div class="body"><pre class="report">${escapeHtml(d.valoracion_en)}</pre></div>
    </details>

    <details open>
      <summary>Extracted variables — review (${d.progress.reviewed}/${d.progress.total})</summary>
      <div class="body">
        ${renderReviewTable(d.idx, d.final_output, d.review || {})}
        <div class="section-title">Raw final_output</div>
        <pre class="code">${escapeHtml(d.final_output_raw)}</pre>
      </div>
    </details>

    <details>
      <summary>Extraction reasoning</summary>
      <div class="body"><pre class="report">${escapeHtml(d.reasoning) || '<span class="na">No reasoning captured.</span>'}</pre></div>
    </details>
  `;
  attachVerdictHandlers(d.idx);
}

async function updateStatsPanel() {
  const s = await loadStats();
  const el = document.getElementById("stats-panel");
  if (!el) return;
  el.innerHTML = `
    <div><span>Total reports</span><b>${s.total_reports}</b></div>
    <div><span>Reports started</span><b>${s.reports_started}</b></div>
    <div><span>Reports fully reviewed</span><b>${s.reports_complete}</b></div>
    <div><span>Fields reviewed</span><b>${s.total_reviewed} / ${s.total_fields}</b></div>
    <div><span>Correct</span><b>${s.total_correct} / ${s.total_reviewed}</b></div>
  `;
}

function insertStatsPanel() {
  const aside = document.querySelector("aside header");
  const div = document.createElement("div");
  div.className = "stats";
  div.id = "stats-panel";
  div.style.margin = "10px 14px 0";
  aside.insertAdjacentElement("afterend", div);
}

insertStatsPanel();
updateStatsPanel();
loadPatients();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=HTTPStatus.OK):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._send_html(INDEX_HTML)

        if path == "/api/patients":
            out = []
            for i, r in enumerate(ROWS):
                summary = patient_summary(r, i)
                summary["progress"] = row_progress(i)
                out.append(summary)
            return self._send_json(out)

        if path == "/api/stats":
            return self._send_json(compute_stats())

        if path.startswith("/api/patient/"):
            try:
                idx = int(path.rsplit("/", 1)[-1])
                row = ROWS[idx]
            except (ValueError, IndexError):
                return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._send_json(patient_detail(row, idx))

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        path = urlparse(self.path).path

        if path.startswith("/api/review/"):
            try:
                idx = int(path.rsplit("/", 1)[-1])
                ROWS[idx]
            except (ValueError, IndexError):
                return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                field = payload["field"]
                correct = bool(payload["correct"])
            except (json.JSONDecodeError, KeyError, ValueError):
                return self._send_json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)

            valid_fields = fields_for_row(idx)
            if field not in valid_fields:
                return self._send_json({"error": f"unknown field '{field}' for row {idx}"}, HTTPStatus.BAD_REQUEST)

            with REVIEWS_LOCK:
                REVIEWS.setdefault(str(idx), {})[field] = correct
                save_reviews_locked()

            return self._send_json({"ok": True, "progress": row_progress(idx)})

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--file", default=str(DEFAULT_CSV), help="CSV produced by run.py")
    ap.add_argument("-r", "--reviews-file", default=None,
                     help="Path to JSON file storing review verdicts (default: <csv>.reviews.json next to the CSV)")
    ap.add_argument("-p", "--port", type=int, default=8001)
    ap.add_argument("-H", "--host", default="127.0.0.1")
    args = ap.parse_args()

    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    global ROWS, REVIEWS, REVIEWS_PATH
    ROWS = load_csv(csv_path)
    print(f"Loaded {len(ROWS)} rows from {csv_path}")

    REVIEWS_PATH = Path(args.reviews_file) if args.reviews_file else csv_path.with_suffix(".reviews.json")
    REVIEWS = load_reviews(REVIEWS_PATH)
    print(f"Loaded review verdicts from {REVIEWS_PATH} ({sum(len(v) for v in REVIEWS.values())} fields reviewed)")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
