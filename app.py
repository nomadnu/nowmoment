from flask import Flask, jsonify, request
import requests
import re
import threading
import time
import math

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

# ── 메모리 캐시 ──────────────────────────────
_stream_cache = {}
_crawl_status = {
    "running":    False,
    "total":      0,
    "done":       0,
    "found":      0,
    "started_at": None,
}

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({
        "status":  "ok",
        "service": "UTIC CCTV Proxy v8",
        "cached":  len(_stream_cache),
        "crawl":   _crawl_status,
    })

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 팝업 HTML에서 스트림 URL 추출
# ──────────────────────────────────────────────
def _fetch_popup_stream(cctv_id: str) -> str:
    endpoints = [
        f"{BASE_URL}/map/openDataCctvStream.do?cctvId={cctv_id}&key={UTIC_KEY}",
        f"{BASE_URL}/map/cctvStream.do?cctvId={cctv_id}",
    ]
    for ep in endpoints:
        try:
            resp = requests.get(
                ep,
                headers={**HEADERS, "Accept": "text/html"},
                timeout=8
            )
            if resp.status_code != 200:
                continue
            html = resp.text
            patterns = [
                r'(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)',
                r'file[=:]\s*["\']?(https?://[^\s\'"<>&]+)',
                r'src[=:]\s*["\']?(https?://[^\s\'"<>&]+\.m3u8)',
            ]
            for pat in patterns:
                for m in re.findall(pat, html, re.IGNORECASE):
                    if m.startswith("http") and (
                            "m3u8" in m or "stream" in m):
                        return m
        except Exception:
            continue
    return ""

def get_stream_url(data: dict) -> str:
    if data.get("MOVIE") != "Y":
        return ""
    kind    = data.get("KIND", "")
    cctv_id = data.get("CCTVID", "")
    # KB(KBS 재난포털), A(서울 ASX), N/H(ITS) 는 스킵
    if kind in ("KB", "A", "N", "H"):
        return ""
    return _fetch_popup_stream(cctv_id)

# ──────────────────────────────────────────────
# 크롤러 백그라운드 작업
# ──────────────────────────────────────────────
def _crawl_worker(cctv_ids: list):
    global _crawl_status

    _crawl_status.update({
        "running":    True,
        "total":      len(cctv_ids),
        "done":       0,
        "found":      0,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    for cctv_id in cctv_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/map/getCctvInfoById.do",
                params={"cctvId": cctv_id},
                headers=HEADERS,
                timeout=8
            )
            data = resp.json()
            url  = get_stream_url(data)

            if url:
                _stream_cache[cctv_id] = {
                    "url":    url,
                    "kind":   data.get("KIND", ""),
                    "name":   data.get("CCTVNAME", ""),
                    "lat":    data.get("YCOORD", 0),
                    "lng":    data.get("XCOORD", 0),
                    "center": data.get("CENTERNAME", ""),
                }
                _crawl_status["found"] += 1
        except Exception:
            pass

        _crawl_status["done"] += 1
        time.sleep(0.3)  # 서버 부하 방지

    _crawl_status["running"] = False

# ──────────────────────────────────────────────
# 크롤링 시작 (GET 방식 — 브라우저에서 바로 실행)
# GET /crawl/start?ids=L904028,L904029,L904030
# ──────────────────────────────────────────────
@app.route("/crawl/start")
def crawl_start():
    if _crawl_status["running"]:
        return jsonify({
            "error":  "이미 크롤링 중",
            "status": _crawl_status,
        }), 400

    ids_str  = request.args.get("ids", "")
    cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()]

    if not cctv_ids:
        return jsonify({
            "error":   "ids 파라미터 필요",
            "example": "/crawl/start?ids=L904028,L904029,L904030",
        }), 400

    t = threading.Thread(
        target=_crawl_worker,
        args=(cctv_ids,),
        daemon=True
    )
    t.start()

    return jsonify({
        "status":  "시작됨",
        "total":   len(cctv_ids),
        "message": "아래 URL에서 진행상황을 확인하세요",
        "check":   "https://nowmoment.onrender.com/crawl/status",
    })

# ──────────────────────────────────────────────
# 진행 상황 확인
# GET /crawl/status
# ──────────────────────────────────────────────
@app.route("/crawl/status")
def crawl_status():
    return jsonify({
        **_crawl_status,
        "cached_count": len(_stream_cache),
        "progress":     f"{_crawl_status['done']}/{_crawl_status['total']}",
    })

# ──────────────────────────────────────────────
# 수집 결과 확인
# GET /crawl/result
# ──────────────────────────────────────────────
@app.route("/crawl/result")
def crawl_result():
    return jsonify({
        "count":   len(_stream_cache),
        "streams": _stream_cache,
    })

# ──────────────────────────────────────────────
# 앱에서 사용할 엔드포인트
# GET /api/cctv?lat=37.79&lng=128.89&radius=5
# ──────────────────────────────────────────────
@app.route("/api/cctv")
def api_cctv():
    try:
        lat    = float(request.args.get("lat", 0))
        lng    = float(request.args.get("lng", 0))
        radius = float(request.args.get("radius", 5))

        if lat == 0 or lng == 0:
            return jsonify({"error": "lat, lng 파라미터 필요"}), 400

        def dist_km(la1, ln1, la2, ln2):
            R    = 6371
            dlat = math.radians(la2 - la1)
            dlng = math.radians(ln2 - ln1)
            a    = (math.sin(dlat/2)**2 +
                    math.cos(math.radians(la1)) *
                    math.cos(math.radians(la2)) *
                    math.sin(dlng/2)**2)
            return R * 2 * math.asin(math.sqrt(a))

        nearby = []
        for cctv_id, info in _stream_cache.items():
            d = dist_km(lat, lng, info.get("lat", 0), info.get("lng", 0))
            if d <= radius:
                nearby.append({
                    "cctvId":    cctv_id,
                    "name":      info["name"],
                    "lat":       info["lat"],
                    "lng":       info["lng"],
                    "streamUrl": info["url"],
                    "kind":      info["kind"],
                    "center":    info["center"],
                    "distKm":    round(d, 2),
                })

        nearby.sort(key=lambda x: x["distKm"])
        return jsonify({"count": len(nearby), "items": nearby[:20]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 단일 CCTV 정보 + 스트림 URL 조회
# GET /utic/info?cctvId=L904028
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
            headers=HEADERS, timeout=10
        )
        data = resp.json()
        url  = get_stream_url(data)
        return jsonify({
            "cctvId":    cctv_id,
            "data":      data,
            "streamUrl": url,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
