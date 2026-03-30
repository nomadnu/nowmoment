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
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v5"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# JS 전체에서 KB/KBS 관련 함수 찾기
# ──────────────────────────────────────────────
@app.route("/utic/kb_full")
def kb_full():
    try:
        resp = requests.get(
            f"{BASE_URL}/js/openDataCctvStream.js",
            headers=HEADERS, timeout=10
        )
        text = resp.text

        # KB 또는 cctvPlay_K 함수 주변 코드 추출
        # 함수 이름 패턴: cctvPlay_KB, cctvPlay_K, KIND == 'KB'
        patterns = [
            r'KB',
            r'cctvPlay_K[B]?',
            r"KIND.*KB",
            r"'KB'",
            r'"KB"',
            r'kbs',
            r'KBS',
            r'disaster',
            r'news\.kbs',
        ]

        found = {}
        for pat in patterns:
            matches = [(m.start(), text[max(0,m.start()-100):m.start()+300])
                      for m in re.finditer(pat, text, re.IGNORECASE)]
            if matches:
                found[pat] = [m[1] for m in matches[:5]]

        # cctvPlay_ 함수 목록 전체 추출
        play_funcs = re.findall(
            r'this\.cctvPlay_(\w+)\s*=\s*function', text)

        return jsonify({
            "play_functions": play_funcs,
            "kb_matches":     found,
            "js_length":      len(text),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# UTIC 팝업 페이지에서 실제 스트림 URL 추출
# CCTV 클릭 시 열리는 팝업 페이지 분석
# ──────────────────────────────────────────────
@app.route("/utic/popup")
def utic_popup():
    cctv_id = request.args.get("cctvId", "L933092")

    popup_urls = [
        f"{BASE_URL}/map/popupCctvStream.do?cctvId={cctv_id}",
        f"{BASE_URL}/map/cctvPopup.do?cctvId={cctv_id}",
        f"{BASE_URL}/guide/cctvStream.do?cctvId={cctv_id}&key={UTIC_KEY}",
        f"{BASE_URL}/guide/popupCctv.do?cctvId={cctv_id}&key={UTIC_KEY}",
    ]

    results = []
    for url in popup_urls:
        try:
            resp = requests.get(url, headers={
                **HEADERS, "Accept": "text/html"}, timeout=8)
            # URL이나 스트림 관련 내용 추출
            body = resp.text
            urls_in_body = re.findall(
                r'(?:http[s]?://[^\s\'"<>]+(?:m3u8|rtmp|rtsp|stream)[^\s\'"<>]*)',
                body, re.IGNORECASE)
            results.append({
                "url":          url,
                "status":       resp.status_code,
                "ct":           resp.headers.get("Content-Type",""),
                "stream_urls":  urls_in_body,
                "body_preview": body[:400],
            })
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return jsonify(results)

# ──────────────────────────────────────────────
# KBS 재난포털 CCTV 스트림 직접 테스트
# STRMID와 CCTVIP(포트)로 다양한 패턴 시도
# ──────────────────────────────────────────────
@app.route("/kbs/probe")
def kbs_probe():
    strm_id = request.args.get("strmId", "L933092")
    port    = request.args.get("port",   "9983")    # CCTVIP 값

    # KBS 재난포털이 사용하는 알려진 스트리밍 서버들
    candidates = [
        # KBS CDN 패턴
        f"https://news.kbs.co.kr/special/emergency/2020/earthquake/cctv/{strm_id}.m3u8",
        f"http://cctv.kbs.co.kr/stream/{strm_id}.m3u8",
        f"http://kbscctv.kbs.co.kr/{strm_id}/{strm_id}.m3u8",
        # 포트 기반 패턴
        f"rtmp://streaming.kbs.co.kr:{port}/live/{strm_id}",
        # UTIC 자체 스트리밍
        f"http://streaming.utic.go.kr/live/{strm_id}.m3u8",
        f"http://stream.utic.go.kr/{strm_id}.m3u8",
        # 공공 재난 스트리밍
        f"http://cctv.safekorea.go.kr/stream/{strm_id}.m3u8",
    ]

    results = []
    for url in candidates:
        if url.startswith("rtmp"):
            results.append({"url": url, "note": "RTMP - 앱에서 미지원"})
            continue
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            results.append({
                "url":    url,
                "status": resp.status_code,
                "ct":     resp.headers.get("Content-Type", ""),
            })
        except Exception as e:
            results.append({"url": url, "error": str(e[:100])})

    return jsonify({"strmId": strm_id, "port": port, "results": results})

# ──────────────────────────────────────────────
# 앱에서 사용할 최종 CCTV 엔드포인트
# 위경도 기반으로 CCTV 목록 + 스트림 URL 반환
# GET /api/cctv?lat=37.53&lng=126.92
# ──────────────────────────────────────────────
@app.route("/api/cctv")
def api_cctv():
    try:
        lat    = float(request.args.get("lat", 0))
        lng    = float(request.args.get("lng", 0))
        radius = float(request.args.get("radius", 5))

        if lat == 0 or lng == 0:
            return jsonify({"error": "lat, lng 파라미터 필요"}), 400

        return jsonify({
            "status":  "준비중",
            "message": "스트림 URL 패턴 확인 후 구현 예정",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
