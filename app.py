from flask import Flask, jsonify, request, Response
import requests
import re
import json

app = Flask(__name__)

UTIC_KEY = "ZVLJkMXJRVVi9UMJoSlmD3cH9D6vS2FYihW68QH2JDM"
BASE_URL  = "http://www.utic.go.kr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
}

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v2"})

# ──────────────────────────────────────────────
# 이 서버의 아웃바운드 IP 확인
# ──────────────────────────────────────────────
@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# UTIC JS 파일 분석 - 내부 API 엔드포인트 탐색
# ──────────────────────────────────────────────
@app.route("/utic/js")
def utic_js():
    try:
        resp = requests.get(
            f"{BASE_URL}/js/openDataCctvStream.js",
            headers=HEADERS, timeout=10
        )
        return Response(resp.text, content_type="text/plain; charset=utf-8")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# UTIC CCTV 목록 조회 (여러 엔드포인트 시도)
# GET /cctv?minX=126.9&maxX=127.1&minY=37.4&maxY=37.6
# ──────────────────────────────────────────────
@app.route("/cctv")
def cctv():
    min_x = request.args.get("minX", "")
    max_x = request.args.get("maxX", "")
    min_y = request.args.get("minY", "")
    max_y = request.args.get("maxY", "")

    # 시도할 UTIC 내부 API 엔드포인트 목록
    endpoints = [
        # 형식 1: JSON API
        {
            "url": f"{BASE_URL}/guide/getCctvList.do",
            "params": {"key": UTIC_KEY, "type": "json",
                       "minX": min_x, "maxX": max_x,
                       "minY": min_y, "maxY": max_y},
        },
        # 형식 2: CCTV 스트림 목록
        {
            "url": f"{BASE_URL}/guide/cctvDataList.do",
            "params": {"key": UTIC_KEY,
                       "minX": min_x, "maxX": max_x,
                       "minY": min_y, "maxY": max_y},
        },
        # 형식 3: openData Ajax
        {
            "url": f"{BASE_URL}/openData/getCctvList.do",
            "params": {"key": UTIC_KEY, "type": "json",
                       "minX": min_x, "maxX": max_x,
                       "minY": min_y, "maxY": max_y},
        },
        # 형식 4: utis CCTV API
        {
            "url": f"{BASE_URL}/utic/getCctvList.do",
            "params": {"key": UTIC_KEY,
                       "minX": min_x, "maxX": max_x,
                       "minY": min_y, "maxY": max_y},
        },
        # 형식 5: 키만 파라미터
        {
            "url": f"{BASE_URL}/guide/cctvOpenData.do",
            "params": {"key": UTIC_KEY, "type": "json",
                       "minX": min_x, "maxX": max_x,
                       "minY": min_y, "maxY": max_y},
        },
    ]

    results = []
    for ep in endpoints:
        try:
            resp = requests.get(
                ep["url"],
                params={k: v for k, v in ep["params"].items() if v},
                headers=HEADERS,
                timeout=10
            )
            ct = resp.headers.get("Content-Type", "")
            results.append({
                "url":          ep["url"],
                "status":       resp.status_code,
                "content_type": ct,
                "preview":      resp.text[:300],
            })
        except Exception as e:
            results.append({"url": ep["url"], "error": str(e)})

    return jsonify(results)

# ──────────────────────────────────────────────
# UTIC 원본 HTML 페이지에서 AJAX URL 파싱
# ──────────────────────────────────────────────
@app.route("/utic/parse")
def utic_parse():
    try:
        # 원본 페이지 로드
        resp = requests.get(
            f"{BASE_URL}/guide/cctvOpenData.do",
            params={"key": UTIC_KEY},
            headers=HEADERS,
            timeout=15
        )
        html = resp.text

        # JS 파일에서 Ajax URL 패턴 추출
        js_resp = requests.get(
            f"{BASE_URL}/js/openDataCctvStream.js",
            headers=HEADERS, timeout=10
        )
        js_text = js_resp.text

        # URL 패턴 찾기
        url_patterns = re.findall(r'["\']([^"\']*\.do[^"\']*)["\']', js_text)
        ajax_urls    = re.findall(r'url\s*:\s*["\']([^"\']+)["\']', js_text)
        fetch_urls   = re.findall(
            r'(?:fetch|ajax|get|post)\s*\(\s*["\']([^"\']+)["\']', js_text)

        return jsonify({
            "url_patterns": url_patterns[:30],
            "ajax_urls":    ajax_urls[:20],
            "fetch_urls":   fetch_urls[:20],
            "js_preview":   js_text[:1000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
