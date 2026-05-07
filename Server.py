from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from Q_simulator import QuantumChannelSimulator
import uuid
import time
import os
import json

app = Flask(__name__)
CORS(app)

# ---------------------------------------
# Global State
# ---------------------------------------
ONLINE = set()
qc = QuantumChannelSimulator(shots=512, trust_threshold=0.04)
SESSIONS = {}
TRANSFER_LOG = []   # live feed for dashboard

# ---------------------------------------
# Dashboard UI
# ---------------------------------------
@app.route("/")
def dashboard():
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"), encoding="utf-8").read()
    return Response(html, mimetype="text/html")

@app.route("/dashboard_data", methods=["GET"])
def dashboard_data():
    return jsonify({
        "online": list(ONLINE),
        "sessions": [
            {"id": sid[:8], "sender": s["sender"], "receiver": s["receiver"],
             "trust": round(s["trust"], 4), "bit_error": round(s["bit_error"], 4),
             "ready": s["receiver_ready"], "age": round(time.time() - s["created"], 1)}
            for sid, s in list(SESSIONS.items())[-20:]
        ],
        "log": TRANSFER_LOG[-30:]
    })

# SSE stream for real-time push
@app.route("/stream")
def stream():
    def event_stream():
        last = 0
        while True:
            if len(TRANSFER_LOG) > last:
                for entry in TRANSFER_LOG[last:]:
                    yield f"data: {json.dumps(entry)}\n\n"
                last = len(TRANSFER_LOG)
            time.sleep(0.3)
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ---------------------------------------
# 1) Sender requests channel creation
# ---------------------------------------
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

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

        if result.get("trust", 0) < qc.trust_threshold:
            print(f"[QC] Channel rejected: {sender} -> {receiver} | "
                  f"Trust: {result['trust']:.4f} | Bit Error: {result['bit_error']:.4f}")

            return jsonify({
                "status": "REJECTED",
                "trust": result["trust"],
                "bit_error": result["bit_error"]
            }), 403

        session_id = str(uuid.uuid4())
        receiver_ready = receiver in ONLINE

        SESSIONS[session_id] = {
            "sender": sender,
            "receiver": receiver,
            "trust": result["trust"],
            "bit_error": result["bit_error"],
            "receiver_ready": receiver_ready,
            "created": time.time()
        }

        TRANSFER_LOG.append({
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
            ONLINE.add(name)

        return {"status": "OK"}

    except Exception as e:
        return {"status": "ERROR", "message": str(e)}, 400


# ---------------------------------------
# 2) Receiver announces readiness
# ---------------------------------------
@app.route("/qc_confirm/<session_id>", methods=["POST"])
def qc_confirm(session_id):
    s = SESSIONS.get(session_id)

    if not s:
        return jsonify({"status": "INVALID"}), 404

    s["receiver_ready"] = True
    TRANSFER_LOG.append({
        "time": time.strftime("%H:%M:%S"),
        "event": "TRANSMITTED",
        "sender": s["sender"],
        "receiver": s["receiver"],
        "trust": round(s["trust"], 4),
        "bit_error": round(s["bit_error"], 4),
        "session": session_id[:8]
    })
    print(f"[QC] Receiver ready for session {session_id}")

    return jsonify({"status": "READY"}), 200


# ---------------------------------------
# 3) Sender checks if transmission allowed
# ---------------------------------------
@app.route("/qc_status/<session_id>", methods=["GET"])
def qc_status(session_id):
    s = SESSIONS.get(session_id)

    if not s:
        return jsonify({"status": "INVALID"}), 404

    if s["receiver_ready"]:
        print(f"[QC] Transmission unlocked {session_id}")
        return jsonify({
            "status": "TRANSMIT",
            "trust": s["trust"],
            "bit_error": s["bit_error"]
        }), 200

    return jsonify({"status": "WAIT"}), 200


# ---------------------------------------
# 4) Cleanup old sessions (optional safety)
# ---------------------------------------
@app.route("/qc_cleanup", methods=["POST"])
def qc_cleanup():
    now = time.time()
    removed = []

    for sid in list(SESSIONS.keys()):
        if now - SESSIONS[sid]["created"] > 60:
            removed.append(sid)
            del SESSIONS[sid]

    return jsonify({"removed": removed})


# ---------------------------------------
# START SERVER
# ---------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5003))
    print("[QC SERVER] Quantum Channel Broker Active")
    print(f"[QC SERVER] Listening on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
