from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

UTIC_KEY = "ZVLJkMXJRVVi9UMJoSlmD3cH9D6vS2FYihW68QH2JDM"
UTIC_URL = "http://www.utic.go.kr/guide/cctvOpenData.do"

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy"})

# 이 서버의 실제 아웃바운드 IP 확인용
@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/cctv")
def cctv():
    try:
        resp = requests.get(
            UTIC_URL,
            params={"key": UTIC_KEY},
            timeout=20
        )
        data = resp.json()
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
