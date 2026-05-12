"""Simple stdlib HTTP server for browsing BTRecordsLLM output rows.

Run:
    python3 server.py            # serves on http://127.0.0.1:8000
    python3 server.py -p 9000    # custom port
    python3 server.py -f path/to/test-100.csv

The page lists every row from the CSV and shows, for the selected row:
- patient metadata
- collapsible original Spanish report
- English translation
- translation thinking (collapsible)
- extracted clinical variables (parsed from final_output JSON)
- extraction reasoning (collapsible)
"""

import argparse
import csv
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

csv.field_size_limit(sys.maxsize)

DEFAULT_CSV = Path(__file__).parent / "output" / "test-100.csv"

# Columns we surface in the UI. Anything else is ignored.
META_COLS = [
    "info_key",
    "sip",
    "fechaNaci",
    "fechaHoraRealizacion",
    "modalidad",
    "prestacionCentro",
    "cancer",
    "timepoint",
    "diagnosticos_encontrados",
    "localizaciones_encontradas",
]

ROWS: list[dict] = []


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_final_output(text: str):
    """final_output is JSON-ish but may be truncated or fenced. Try hard."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Try to recover a JSON object even if trailing braces are missing.
    if cleaned.lstrip().startswith("{"):
        candidate = cleaned
        for _ in range(3):
            candidate = candidate + "}"
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def patient_summary(row: dict, idx: int) -> dict:
    return {
        "idx": idx,
        "info_key": row.get("info_key", ""),
        "sip": row.get("sip", ""),
        "date": row.get("fechaHoraRealizacion", ""),
        "modality": row.get("modalidad", ""),
        "prestacion": row.get("prestacionCentro", ""),
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
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BTRecords Viewer</title>
<style>
  :root {
    --bg: #f7f7f8;
    --panel: #ffffff;
    --border: #e2e2e6;
    --muted: #6b7280;
    --accent: #2563eb;
    --accent-soft: #dbeafe;
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
  .patient .pid { font-weight: 600; }
  .patient .sub { color: var(--muted); font-size: 11px; margin-top: 2px; }

  main { flex: 1; overflow-y: auto; padding: 22px 28px; }
  main .empty { color: var(--muted); margin-top: 80px; text-align: center; }

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
  .vars { display: grid; grid-template-columns: minmax(220px, 280px) 1fr; gap: 6px 14px; font-size: 13px; }
  .vars dt { font-weight: 600; color: #1f2937; padding: 6px 0; border-bottom: 1px dashed var(--border); }
  .vars dd { margin: 0; padding: 6px 0; border-bottom: 1px dashed var(--border); white-space: pre-wrap; }
  .vars .na { color: var(--muted); font-style: italic; }
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
    <h1>BTRecords Viewer</h1>
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

async function loadPatients() {
  const res = await fetch("/api/patients");
  patients = await res.json();
  document.getElementById("count").textContent = patients.length + " reports";
  renderList("");
}

function renderList(q) {
  const list = document.getElementById("patients");
  const ql = q.trim().toLowerCase();
  const filtered = !ql ? patients : patients.filter(p =>
    [p.info_key, p.sip, p.date, p.modality, p.prestacion].some(v => String(v ?? "").toLowerCase().includes(ql))
  );
  list.innerHTML = filtered.map(p => `
    <div class="patient ${p.idx === activeIdx ? "active" : ""}" data-idx="${p.idx}">
      <div class="pid">#${escapeHtml(p.info_key)} <span style="color:var(--muted);font-weight:400">· sip ${escapeHtml(p.sip)}</span></div>
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

function renderVarsTable(obj) {
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
    return `<dt>${escapeHtml(k)}</dt><dd>${rendered}</dd>`;
  }).join("");
  return `<dl class="vars">${rows}</dl>`;
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
      <summary>Extracted clinical variables</summary>
      <div class="body">
        ${renderVarsTable(d.final_output)}
        <div class="section-title">Raw final_output</div>
        <pre class="code">${escapeHtml(d.final_output_raw)}</pre>
      </div>
    </details>

    <details>
      <summary>Extraction reasoning</summary>
      <div class="body"><pre class="report">${escapeHtml(d.reasoning) || '<span class="na">No reasoning captured.</span>'}</pre></div>
    </details>
  `;
}

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
            return self._send_json([patient_summary(r, i) for i, r in enumerate(ROWS)])

        if path.startswith("/api/patient/"):
            try:
                idx = int(path.rsplit("/", 1)[-1])
                row = ROWS[idx]
            except (ValueError, IndexError):
                return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._send_json(patient_detail(row, idx))

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--file", default=str(DEFAULT_CSV), help="CSV produced by run.py (default: output/test-100.csv)")
    ap.add_argument("-p", "--port", type=int, default=8000)
    ap.add_argument("-H", "--host", default="127.0.0.1")
    args = ap.parse_args()

    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    global ROWS
    ROWS = load_csv(csv_path)
    print(f"Loaded {len(ROWS)} rows from {csv_path}")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
