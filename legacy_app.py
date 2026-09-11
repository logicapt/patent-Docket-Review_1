import os
import io
import json
import logging
from flask import Flask, render_template, request, jsonify, Response, send_file
from werkzeug.utils import secure_filename

from core.excel_parser import parse_excel_file
from core.batch_processor import BatchProcessor
from core.export_manager import generate_enriched_excel, generate_csv
from core.proxy_manager import ProxyManager
from core.court_listener import CourtListenerClient
from core.keyword_engine import LEGAL_GLOSSARY
from core.keyword_engine import KeywordEngine
from core.docket_imports import DocketImportStore, DocketImportError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

current_cases = []
docket_imports = DocketImportStore(os.path.join(app.config["UPLOAD_FOLDER"], "docket_imports"))
batch_processor = BatchProcessor(import_store=docket_imports)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/glossary", methods=["GET"])
def get_glossary():
    grouped = {}
    for key, val in LEGAL_GLOSSARY.items():
        phase = val["phase"]
        if phase not in grouped:
            grouped[phase] = []
        grouped[phase].append({
            "key": key,
            "term": val["term"],
            "definition": val["definition"],
            "sample_pattern": val["patterns"][0] if val["patterns"] else ""
        })
    return jsonify({"success": True, "glossary": grouped, "total_terms": len(LEGAL_GLOSSARY)})

@app.route("/api/test-gemini", methods=["POST"])
def test_gemini():
    data = request.json or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return jsonify({"success": False, "error": "No Gemini API key provided"}), 400

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Say 'Gemini API Connected Successfully for Patent Docket Tracker!'"
        )
        return jsonify({"success": True, "message": resp.text.strip()})
    except Exception as e:
        logger.error(f"Gemini test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/load-sample", methods=["POST"])
def load_sample():
    global current_cases
    if batch_processor.is_running:
        return jsonify({"success": False, "error": "Stop the scan before loading another file."}), 409
    sample_path = os.path.join(os.path.dirname(__file__), "sample_data", "sample_patent_cases.xlsx")
    if not os.path.exists(sample_path):
        return jsonify({"success": False, "error": "Sample file not found"}), 404

    try:
        parsed = parse_excel_file(sample_path)
        current_cases = parsed["cases"]
        batch_processor.results = []
        batch_processor.stats = batch_processor._empty_stats(len(current_cases))
        return jsonify({
            "success": True,
            "filename": "sample_patent_cases.xlsx (100 Cases)",
            "total": parsed["total"],
            "columns": parsed["columns"],
            "column_mapping": parsed["column_mapping"],
            "preview": parsed["cases"][:10],
            "all_cases": parsed["cases"]
        })
    except Exception as e:
        logger.exception("Error loading sample file")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/upload", methods=["POST"])
def upload_file():
    global current_cases
    if batch_processor.is_running:
        return jsonify({"success": False, "error": "Stop the scan before loading another file."}), 409
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    if not (filename.endswith(".xlsx") or filename.endswith(".xls") or filename.endswith(".csv")):
        return jsonify({"success": False, "error": "Unsupported file format. Please upload .xlsx, .xls, or .csv"}), 400

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    try:
        parsed = parse_excel_file(save_path)
        current_cases = parsed["cases"]
        batch_processor.results = []
        batch_processor.stats = batch_processor._empty_stats(len(current_cases))
        return jsonify({
            "success": True,
            "filename": filename,
            "total": parsed["total"],
            "columns": parsed["columns"],
            "column_mapping": parsed["column_mapping"],
            "preview": parsed["cases"][:10],
            "all_cases": parsed["cases"]
        })
    except Exception as e:
        logger.exception("Error parsing uploaded file")
        return jsonify({"success": False, "error": f"Failed to parse file: {str(e)}"}), 500

@app.route("/api/import-docket", methods=["POST"])
def import_docket():
    """Use the selected loaded case, never a client-supplied verification flag."""
    selected = next((case for case in current_cases if str(case.get("id")) == request.form.get("case_id")), None)
    if selected is None:
        return jsonify({"success": False, "error": "Load a case spreadsheet and select a case first."}), 400
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"success": False, "error": "Choose a saved or captured HTML docket."}), 400
    with batch_processor.lock:
        if batch_processor.is_running:
            return jsonify({"success": False, "error": "Stop the scan before importing a docket."}), 409
        try:
            parsed = docket_imports.import_html(file.stream.read(DocketImportStore.MAX_BYTES + 1),
                secure_filename(file.filename), selected, request.form.get("source_url", ""))
            case = {**selected, "docket_import_id": parsed["docket_import_id"]}
            batch_processor.import_store = docket_imports
            item = batch_processor._process_case(case, 0, None, KeywordEngine(), False)
        except DocketImportError as exc:
            return jsonify({"success": False, "error": str(exc), "identity_match": exc.identity}), 422
        selected["docket_import_id"] = parsed["docket_import_id"]
        results = [r for r in batch_processor.results if r.get("id") != item["id"]] + [item]
        batch_processor.results = sorted(results, key=lambda r: r.get("id", 0))
        stats = batch_processor._empty_stats(len(current_cases))
        for result in results:
            stats["processed"] += 1
            if result.get("imported"):
                stats["imported"] += 1
            elif result.get("demo_mode"):
                stats["demo"] += 1
            elif result.get("source_verified"):
                stats["source_verified"] += 1
                key = {"SETTLED": "settled", "DISMISSED": "dismissed", "TERMINATED": "terminated_other"}.get(result.get("status_category"))
                if key:
                    stats[key] += 1
                if result.get("has_invalidity_contentions"):
                    stats["invalidity_contentions"] += 1
            else:
                stats["unmatched"] += 1
        batch_processor.stats = stats
        return jsonify({"success": True, "item": item, "stats": stats,
                        "docket_import_id": parsed["docket_import_id"], "dated_entries": len(parsed["docket_entries"])})


@app.route("/api/docket-imports/<import_id>/evidence")
def download_imported_evidence(import_id):
    try:
        path = docket_imports.path_for(import_id)
        if not path.is_file():
            raise DocketImportError("Imported docket not found.")
    except DocketImportError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    return send_file(path, mimetype="application/json", as_attachment=True,
                     download_name="docket_evidence_" + import_id + ".json")


@app.route("/api/start-scan", methods=["POST"])
def start_scan():
    global current_cases, batch_processor
    data = request.json or {}
    
    cases_to_scan = data.get("cases") or current_cases
    if not cases_to_scan:
        return jsonify({"success": False, "error": "No case data loaded to scan"}), 400

    api_token = data.get("api_token", "").strip()
    gemini_api_key = data.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "")
    proxy_string = data.get("proxy_string", "").strip()
    pm_cookie = data.get("pm_cookie", "").strip()
    demo_mode = data.get("demo_mode", False)
    if not isinstance(demo_mode, bool):
        return jsonify({"success": False, "error": "demo_mode must be true or false"}), 400
    try:
        delay = float(data.get("delay", 0.4))
        if not 0 <= delay <= 60:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "delay must be a number between 0 and 60 seconds"}), 400
    if not isinstance(cases_to_scan, list) or not all(isinstance(case, dict) for case in cases_to_scan):
        return jsonify({"success": False, "error": "cases must be a list of case objects"}), 400
    custom_keywords = data.get("custom_keywords")

    res = batch_processor.start_batch(
        cases=cases_to_scan,
        api_token=api_token,
        gemini_api_key=gemini_api_key,
        proxy_string=proxy_string,
        pm_cookie=pm_cookie,
        custom_keywords=custom_keywords,
        delay_seconds=delay,
        demo_mode=demo_mode
    )
    return jsonify(res)

@app.route("/api/stop-scan", methods=["POST"])
def stop_scan():
    global batch_processor
    return jsonify(batch_processor.stop_batch())

@app.route("/api/progress-stream")
def progress_stream():
    def generate():
        while True:
            try:
                event = batch_processor.event_queue.get(timeout=25.0)
                event_type = event.get("type", "message")
                data_str = json.dumps(event)
                yield f"event: {event_type}\ndata: {data_str}\n\n"
                
                if event_type in ["batch_finished", "batch_cancelled"]:
                    break
            except Exception:
                yield ": keep-alive\n\n"

    return Response(generate(), mimetype="text/event-stream")

@app.route("/api/results", methods=["GET"])
def get_results():
    global batch_processor
    return jsonify({
        "success": True,
        "results": batch_processor.results,
        "stats": batch_processor.stats,
        "is_running": batch_processor.is_running
    })

@app.route("/api/export/excel", methods=["GET"])
def export_excel():
    global batch_processor
    if not batch_processor.results:
        return jsonify({"error": "No results available to export"}), 400

    excel_io = generate_enriched_excel(batch_processor.results)
    return send_file(
        excel_io,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="patent_case_status_report.xlsx"
    )

@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    global batch_processor
    if not batch_processor.results:
        return jsonify({"error": "No results available to export"}), 400

    csv_data = generate_csv(batch_processor.results)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=patent_case_status_report.csv"}
    )

@app.route("/api/test-proxy", methods=["POST"])
def test_proxy():
    data = request.json or {}
    proxy_str = data.get("proxy_string", "").strip()
    mgr = ProxyManager(proxy_str)
    res = mgr.test_proxy()
    return jsonify(res)

if __name__ == "__main__":
    print("\n" + "="*70)
    print(" PATENT CASE DOCKET & STATUS TRACKER")
    print(" Web Interface running at: http://127.0.0.1:5000")
    print("="*70 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
