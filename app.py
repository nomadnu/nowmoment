from flask import Flask, jsonify, request, Response
import requests

app = Flask(__name__)

UTIC_KEY = "ZVLJkMXJRVVi9UMJoSlmD3cH9D6vS2FYihW68QH2JDM"
UTIC_URL = "http://www.utic.go.kr/guide/cctvOpenData.do"

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 원본 응답 확인용 (형식 그대로 반환)
@app.route("/cctv/raw")
def cctv_raw():
    try:
        resp = requests.get(
            UTIC_URL,
            params={"key": UTIC_KEY},
            timeout=20
        )
        content_type = resp.headers.get("Content-Type", "text/plain")
        return Response(resp.text, status=resp.status_code,
                        content_type="text/plain; charset=utf-8")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 앱에서 사용할 JSON 엔드포인트
@app.route("/cctv")
def cctv():
    try:
        resp = requests.get(
            UTIC_URL,
            params={"key": UTIC_KEY},
            timeout=20
        )

        content_type = resp.headers.get("Content-Type", "")

        # JSON 응답
        if "json" in content_type:
            return jsonify(resp.json()), resp.status_code

        # XML 응답 → 그대로 반환
        if "xml" in content_type:
            return Response(resp.text, status=resp.status_code,
                            content_type="application/xml; charset=utf-8")

        # 기타 → 텍스트로 반환 (앞 1000자 로그)
        return jsonify({
            "content_type": content_type,
            "status_code": resp.status_code,
            "preview": resp.text[:1000]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
