from flask import Flask, jsonify, request, Response
import requests
import json
import re

app = Flask(__name__)

UTIC_KEY = "ZVLJkMXJRVVi9UMJoSlmD3cH9D6vS2FYihW68QH2JDM"
BASE_URL  = "http://www.utic.go.kr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v3"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# CCTV ID로 상세 정보 조회 (스트림 URL 포함 여부 확인)
# GET /utic/info?cctvId=L933092
# ──────────────────────────────────────────────
@app.route("/utic/info")
def utic_info():
    cctv_id = request.args.get("cctvId", "")
    if not cctv_id:
        return jsonify({"error": "cctvId 파라미터 필요"}), 400

    try:
        resp = requests.get(
            f"{BASE_URL}/map/getCctvInfoById.do",
            params={"cctvId": cctv_id},
            headers=HEADERS,
            timeout=10
        )
        ct = resp.headers.get("Content-Type", "")
        return jsonify({
            "status":       resp.status_code,
            "content_type": ct,
            "raw":          resp.text[:2000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# CCTV 목록 샘플 조회 (CSV의 첫 10개 ID 테스트)
# GET /utic/sample
# ──────────────────────────────────────────────
@app.route("/utic/sample")
def utic_sample():
    # CSV에 있는 샘플 CCTV ID 목록 (실제 데이터)
    sample_ids = [
        "L933092", "L933065", "L933061", "L933062", "L933102",
        "L933101", "L933066", "L933096", "L933090", "L933097",
    ]

    results = []
    for cctv_id in sample_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/map/getCctvInfoById.do",
                params={"cctvId": cctv_id},
                headers=HEADERS,
                timeout=10
            )
            ct = resp.headers.get("Content-Type", "")

            # JSON 파싱 시도
            data = None
            try:
                data = resp.json()
            except Exception:
                pass

            results.append({
                "cctvId":       cctv_id,
                "status":       resp.status_code,
                "content_type": ct,
                "data":         data,
                "raw":          resp.text[:500] if not data else None,
            })
        except Exception as e:
            results.append({"cctvId": cctv_id, "error": str(e)})

    return jsonify(results)

# ──────────────────────────────────────────────
# UTIC CCTV 전체 목록에서 스트림 가능한 URL 추출
# GET /utic/stream?cctvId=L933092
# ──────────────────────────────────────────────
@app.route("/utic/stream")
def utic_stream():
    cctv_id = request.args.get("cctvId", "")
    if not cctv_id:
        return jsonify({"error": "cctvId 필요"}), 400

    try:
        resp = requests.get(
            f"{BASE_URL}/map/getCctvInfoById.do",
            params={"cctvId": cctv_id},
            headers=HEADERS,
            timeout=10
        )

        try:
            data = resp.json()
        except Exception:
            return jsonify({
                "cctvId":   cctv_id,
                "hasUrl":   False,
                "raw":      resp.text[:500],
                "message":  "JSON 파싱 실패",
            })

        # 스트림 URL 필드 탐색
        stream_url = None
        url_fields = ["CCTVURL", "cctvurl", "streamUrl", "STREAMURL",
                      "url", "URL", "LIVEURL", "liveurl"]

        if isinstance(data, dict):
            for field in url_fields:
                val = data.get(field, "")
                if val and val.startswith("http"):
                    stream_url = val
                    break

        return jsonify({
            "cctvId":    cctv_id,
            "hasUrl":    stream_url is not None,
            "streamUrl": stream_url,
            "allFields": data if isinstance(data, dict) else None,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
