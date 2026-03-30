from flask import Flask, jsonify, request, Response
import requests
import json

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
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v4"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# KBS 스트림 URL 패턴 테스트
# KIND=KB, STRMID=L933092 기준으로 알려진 패턴 시도
# GET /kbs/test?strmId=L933092
# ──────────────────────────────────────────────
@app.route("/kbs/test")
def kbs_test():
    strm_id = request.args.get("strmId", "L933092")

    # KBS 재난포털 CCTV 스트림 URL 후보 패턴들
    candidates = [
        f"http://d2bq2d93iuuv2a.cloudfront.net/cctv/{strm_id}/{strm_id}.m3u8",
        f"http://d2bq2d93iuuv2a.cloudfront.net/live/{strm_id}.m3u8",
        f"https://d2bq2d93iuuv2a.cloudfront.net/cctv/{strm_id}/{strm_id}.m3u8",
        f"http://news.kbs.co.kr/cctv/{strm_id}.m3u8",
        f"http://kbs-cctv.stream.co.kr/{strm_id}.m3u8",
        f"http://www.utic.go.kr/cctv/stream/{strm_id}.m3u8",
    ]

    results = []
    for url in candidates:
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            results.append({
                "url":    url,
                "status": resp.status_code,
                "ct":     resp.headers.get("Content-Type", ""),
            })
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return jsonify({"strmId": strm_id, "candidates": results})

# ──────────────────────────────────────────────
# UTIC 페이지에서 실제 스트림 URL 추출
# UTIC이 KBS CCTV 클릭시 호출하는 실제 URL 탐색
# GET /utic/stream_url?cctvId=L933092
# ──────────────────────────────────────────────
@app.route("/utic/stream_url")
def utic_stream_url():
    cctv_id = request.args.get("cctvId", "L933092")

    # UTIC이 CCTV 스트림을 위해 호출할 수 있는 엔드포인트 후보
    endpoints = [
        f"{BASE_URL}/map/getCctvStreamUrl.do?cctvId={cctv_id}",
        f"{BASE_URL}/map/getCctvUrl.do?cctvId={cctv_id}",
        f"{BASE_URL}/cctv/getStreamUrl.do?cctvId={cctv_id}",
        f"{BASE_URL}/map/cctvStream.do?cctvId={cctv_id}",
        f"{BASE_URL}/guide/getCctvStream.do?cctvId={cctv_id}&key={UTIC_KEY}",
    ]

    results = []
    for url in endpoints:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            results.append({
                "url":    url,
                "status": resp.status_code,
                "ct":     resp.headers.get("Content-Type", ""),
                "body":   resp.text[:300],
            })
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return jsonify(results)

# ──────────────────────────────────────────────
# UTIC 앱 JS에서 KBS 스트림 URL 구성 방식 확인
# GET /utic/kbs_js
# ──────────────────────────────────────────────
@app.route("/utic/kbs_js")
def utic_kbs_js():
    try:
        resp = requests.get(
            f"{BASE_URL}/js/openDataCctvStream.js",
            headers=HEADERS, timeout=10
        )
        # KB 관련 코드만 추출
        text = resp.text
        lines = text.split('\n')
        kb_lines = [l for l in lines if 'KB' in l or 'kbs' in l.lower()
                    or 'stream' in l.lower() or 'm3u8' in l.lower()
                    or 'rtmp' in l.lower() or 'http' in l]
        return jsonify({
            "kb_related_lines": kb_lines[:50],
            "full_js_length": len(text),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 위경도 기반 근처 CCTV 조회 + 스트림 URL 포함
# (앱에서 최종 사용할 엔드포인트)
# GET /cctv?lat=37.53&lng=126.92&radius=5
# ──────────────────────────────────────────────
@app.route("/cctv")
def cctv():
    try:
        lat    = float(request.args.get("lat", 0))
        lng    = float(request.args.get("lng", 0))
        radius = float(request.args.get("radius", 5))

        if lat == 0 or lng == 0:
            return jsonify({"error": "lat, lng 파라미터 필요"}), 400

        # UTIC 전체 목록에서 반경 내 필터링
        resp = requests.get(
            f"{BASE_URL}/guide/cctvOpenData.do",
            params={"key": UTIC_KEY},
            headers={**HEADERS, "Accept": "text/html"},
            timeout=20
        )

        return jsonify({
            "status":  resp.status_code,
            "message": "UTIC HTML 페이지 반환 - REST API 미지원",
            "next":    "CCTVID 기반으로 개별 스트림 URL 조회 필요",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
