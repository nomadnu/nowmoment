from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# UTIC API 키 (환경변수 또는 직접 입력)
UTIC_KEY = "ZVLJkMXJRVVi9UMJoSlmD3cH9D6vS2FYihW68QH2JDM"
UTIC_URL = "http://www.utic.go.kr/guide/cctvOpenData.do"

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy"})

@app.route("/cctv")
def cctv():
    try:
        resp = requests.get(
            UTIC_URL,
            params={"key": UTIC_KEY},
            timeout=15
        )
        data = resp.json()
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
