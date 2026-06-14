from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from Q_simulator import QuantumChannelSimulator
import uuid
import time
import os
import json
import threading

app = Flask(__name__)
CORS(app)

# ---------------------------------------
# Global State (all access guarded by LOCK)
# ---------------------------------------
LOCK = threading.RLock()          # reentrant: log_event may be called while held
ONLINE = {}                       # name -> last heartbeat epoch (TTL-expired)
SESSIONS = {}                     # session_id -> session dict
TRANSFER_LOG = []                 # live feed for dashboard (bounded)

qc = QuantumChannelSimulator(shots=512, trust_threshold=0.35)

ONLINE_TTL = 30                   # a node is "online" if seen within this window (s)
SESSION_TTL = 60                  # sessions older than this are reaped (s)
LOG_MAX = 500                     # cap log growth to bound memory


def online_names():
    """Names whose last heartbeat is within ONLINE_TTL. Call while holding LOCK."""
    now = time.time()
    return [n for n, ts in ONLINE.items() if now - ts <= ONLINE_TTL]


def log_event(entry):
    """Append to the dashboard feed and trim it. Safe to call while holding LOCK."""
    with LOCK:
        TRANSFER_LOG.append(entry)
        if len(TRANSFER_LOG) > LOG_MAX:
            del TRANSFER_LOG[:len(TRANSFER_LOG) - LOG_MAX]


# ---------------------------------------
# Dashboard UI
# ---------------------------------------
@app.route("/")
def dashboard():
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"), encoding="utf-8").read()
    return Response(html, mimetype="text/html")


@app.route("/dashboard_data", methods=["GET"])
def dashboard_data():
    now = time.time()
    with LOCK:
        sessions = [
            {"id": sid[:8], "sender": s["sender"], "receiver": s["receiver"],
             "trust": round(s["trust"], 4), "bit_error": round(s["bit_error"], 4),
             "ready": s["receiver_ready"], "age": round(now - s["created"], 1)}
            for sid, s in list(SESSIONS.items())[-20:]
        ]
        log = TRANSFER_LOG[-30:]
        online = online_names()
    return jsonify({"online": online, "sessions": sessions, "log": log})


# SSE stream for real-time push
@app.route("/stream")
def stream():
    def event_stream():
        last = 0
        while True:
            with LOCK:
                # clamp in case the log was trimmed below the cursor
                last = min(last, len(TRANSFER_LOG))
                pending = TRANSFER_LOG[last:]
                last = len(TRANSFER_LOG)
            for entry in pending:
                yield f"data: {json.dumps(entry)}\n\n"
            time.sleep(0.3)
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})


# ---------------------------------------
# 1) Sender requests channel creation
# ---------------------------------------
@app.route("/qc_request", methods=["POST"])
def qc_request():
    try:
        data = request.get_json(force=True)

        theta = float(data["theta"])
        randomness = float(data["randomness"])
        sender = data["sender"]
        receiver = data["receiver"]

        # Quantum evaluation
        result = qc.transmit(theta=theta, randomness=randomness)

        if result["trust"] < qc.trust_threshold:
            print(f"[QC] Channel rejected: {sender} -> {receiver} | "
                  f"Trust: {result['trust']:.4f} | Bit Error: {result['bit_error']:.4f}")

            # Surface rejections on the dashboard feed too.
            log_event({
                "time": time.strftime("%H:%M:%S"),
                "event": "REJECTED",
                "sender": sender,
                "receiver": receiver,
                "trust": round(result["trust"], 4),
                "bit_error": round(result["bit_error"], 4),
                "session": "-"
            })

            return jsonify({
                "status": "REJECTED",
                "trust": result["trust"],
                "bit_error": result["bit_error"]
            }), 403

        session_id = str(uuid.uuid4())

        with LOCK:
            receiver_ready = receiver in online_names()
            SESSIONS[session_id] = {
                "sender": sender,
                "receiver": receiver,
                "trust": result["trust"],
                "bit_error": result["bit_error"],
                "receiver_ready": receiver_ready,
                "created": time.time()
            }

        log_event({
            "time": time.strftime("%H:%M:%S"),
            "event": "SESSION_CREATED",
            "sender": sender,
            "receiver": receiver,
            "trust": round(result["trust"], 4),
            "bit_error": round(result["bit_error"], 4),
            "session": session_id[:8]
        })

        print(f"[QC] Session created {session_id} : {sender} -> {receiver}")

        return jsonify({
            "status": "APPROVED",
            "session": session_id,
            "trust": result["trust"],
            "bit_error": result["bit_error"]
        }), 200

    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400


# ---------------------------------------
# Heartbeat (marks users online)
# ---------------------------------------
@app.route("/qc_heartbeat", methods=["POST"])
def qc_heartbeat():
    try:
        data = request.get_json(force=True)
        name = data.get("name")

        if name:
            with LOCK:
                ONLINE[name] = time.time()

        return {"status": "OK"}

    except Exception as e:
        return {"status": "ERROR", "message": str(e)}, 400


# ---------------------------------------
# 2) Receiver announces readiness
# ---------------------------------------
@app.route("/qc_confirm/<session_id>", methods=["POST"])
def qc_confirm(session_id):
    with LOCK:
        s = SESSIONS.get(session_id)
        if not s:
            return jsonify({"status": "INVALID"}), 404
        s["receiver_ready"] = True
        sender, receiver = s["sender"], s["receiver"]
        trust, bit_error = s["trust"], s["bit_error"]

    log_event({
        "time": time.strftime("%H:%M:%S"),
        "event": "TRANSMITTED",
        "sender": sender,
        "receiver": receiver,
        "trust": round(trust, 4),
        "bit_error": round(bit_error, 4),
        "session": session_id[:8]
    })
    print(f"[QC] Receiver ready for session {session_id}")

    return jsonify({"status": "READY"}), 200


# ---------------------------------------
# 3) Sender checks if transmission allowed
# ---------------------------------------
@app.route("/qc_status/<session_id>", methods=["GET"])
def qc_status(session_id):
    with LOCK:
        s = SESSIONS.get(session_id)
        if not s:
            return jsonify({"status": "INVALID"}), 404
        ready = s["receiver_ready"]
        trust, bit_error = s["trust"], s["bit_error"]

    if ready:
        print(f"[QC] Transmission unlocked {session_id}")
        return jsonify({
            "status": "TRANSMIT",
            "trust": trust,
            "bit_error": bit_error
        }), 200

    return jsonify({"status": "WAIT"}), 200


# ---------------------------------------
# 4) Cleanup old sessions (manual trigger; also runs automatically below)
# ---------------------------------------
@app.route("/qc_cleanup", methods=["POST"])
def qc_cleanup():
    now = time.time()
    with LOCK:
        removed = [sid for sid in list(SESSIONS.keys())
                   if now - SESSIONS[sid]["created"] > SESSION_TTL]
        for sid in removed:
            del SESSIONS[sid]

    return jsonify({"removed": removed})


# ---------------------------------------
# Background reaper: expire stale sessions and offline nodes
# ---------------------------------------
def reaper():
    while True:
        time.sleep(15)
        now = time.time()
        with LOCK:
            for sid in [s for s, v in SESSIONS.items() if now - v["created"] > SESSION_TTL]:
                del SESSIONS[sid]
            # hard-remove nodes that have been silent far longer than the TTL
            for n in [n for n, ts in ONLINE.items() if now - ts > ONLINE_TTL * 5]:
                del ONLINE[n]


threading.Thread(target=reaper, daemon=True).start()


# ---------------------------------------
# START SERVER
# ---------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5003))
    print("[QC SERVER] Quantum Channel Broker Active")
    print(f"[QC SERVER] Listening on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
