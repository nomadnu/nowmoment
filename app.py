from flask import Flask, jsonify, request, Response
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
    "Accept": "text/html,application/xhtml+xml,*/*",
}
HEADERS_AJAX = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

_stream_cache = {}
_crawl_status = {
    "running": False, "total": 0, "done": 0,
    "found": 0, "started_at": None,
}

@app.route("/")
def health():
    return jsonify({
        "status": "ok", "service": "UTIC CCTV Proxy v10",
        "cached": len(_stream_cache), "crawl": _crawl_status,
    })

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# cctvStream.js 분석 (URL 조합 방식 파악)
# GET /utic/js
# ──────────────────────────────────────────────
@app.route("/utic/js")
def utic_js():
    try:
        resp = requests.get(
            f"{BASE_URL}/js/cctvStream.js?ver=2.3",
            headers=HEADERS, timeout=10
        )
        text = resp.text
        # cctvsec, ktict, m3u8, mp4, stream 관련 라인 추출
        lines = text.split('\n')
        relevant = [l.strip() for l in lines
                    if any(k in l for k in [
                        'cctvsec', 'ktict', 'm3u8', 'mp4',
                        'stream', 'http', 'playlist',
                        'wmsAuth', 'nimble', 'EE',
                    ])]
        return jsonify({
            "length":   len(text),
            "relevant": relevant[:80],
            "preview":  text[:2000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 팝업 HTML 전체 반환 (평문)
# GET /utic/html?cctvId=L904028
# ──────────────────────────────────────────────
@app.route("/utic/html")
def utic_html():
    cctv_id = request.args.get("cctvId", "L904028")
    try:
        info_resp = requests.get(
            f"{BASE_URL}/map/getCctvInfoById.do",
            params={"cctvId": cctv_id},
            headers=HEADERS_AJAX, timeout=10
        )
        data   = info_resp.json()
        kind   = data.get("KIND", "")
        cctvip = str(data.get("CCTVIP", ""))
        name   = data.get("CCTVNAME", "")

        popup_url = (
            f"{BASE_URL}/jsp/map/openDataCctvStream.jsp"
            f"?key={UTIC_KEY}&cctvid={cctv_id}"
            f"&cctvName={requests.utils.quote(name)}"
            f"&kind={kind}&cctvip={cctvip}"
            f"&cctvch=undefined&id=undefined"
            f"&cctvpasswd=undefined&cctvport=undefined"
        )
        popup_resp = requests.get(popup_url, headers=HEADERS, timeout=10)

        # HTML 전체를 텍스트로 반환
        return Response(
            popup_resp.text,
            content_type="text/plain; charset=utf-8"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 팝업 HTML에서 모든 URL 패턴 추출
# GET /utic/popup?cctvId=L904028
# ──────────────────────────────────────────────
@app.route("/utic/popup")
def utic_popup():
    cctv_id = request.args.get("cctvId", "L904028")
    try:
        info_resp = requests.get(
            f"{BASE_URL}/map/getCctvInfoById.do",
            params={"cctvId": cctv_id},
            headers=HEADERS_AJAX, timeout=10
        )
        data   = info_resp.json()
        kind   = data.get("KIND", "")
        cctvip = str(data.get("CCTVIP", ""))
        name   = data.get("CCTVNAME", "")

        popup_url = (
            f"{BASE_URL}/jsp/map/openDataCctvStream.jsp"
            f"?key={UTIC_KEY}&cctvid={cctv_id}"
            f"&cctvName={requests.utils.quote(name)}"
            f"&kind={kind}&cctvip={cctvip}"
            f"&cctvch=undefined&id=undefined"
            f"&cctvpasswd=undefined&cctvport=undefined"
        )
        popup_resp = requests.get(popup_url, headers=HEADERS, timeout=10)
        html = popup_resp.text

        # 모든 URL 패턴 추출
        all_urls = re.findall(
            r'(https?://[^\s\'"<>\\]+)', html, re.IGNORECASE)

        # JS 변수 값 추출
        js_vars = re.findall(
            r'var\s+(\w+)\s*=\s*["\']([^"\']+)["\']', html)
        js_assigns = re.findall(
            r'(\w+)\s*=\s*["\']([^"\']*(?:http|stream|m3u8|mp4)[^"\']*)["\']',
            html, re.IGNORECASE)

        return jsonify({
            "cctvId":     cctv_id,
            "kind":       kind,
            "cctvip":     cctvip,
            "popup_url":  popup_url,
            "all_urls":   list(set(all_urls))[:30],
            "js_vars":    js_vars[:20],
            "js_assigns": js_assigns[:20],
            "html_len":   len(html),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 스트림 URL 추출 함수
# ──────────────────────────────────────────────
def _fetch_stream_from_popup(cctv_id, kind, cctvip, name="") -> str:
    try:
        popup_url = (
            f"{BASE_URL}/jsp/map/openDataCctvStream.jsp"
            f"?key={UTIC_KEY}&cctvid={cctv_id}"
            f"&cctvName={requests.utils.quote(name)}"
            f"&kind={kind}&cctvip={cctvip}"
            f"&cctvch=undefined&id=undefined"
            f"&cctvpasswd=undefined&cctvport=undefined"
        )
        resp = requests.get(popup_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        html = resp.text

        patterns = [
            r'(https?://cctvsec\.ktict\.co\.kr[^\s\'"<>\\]+)',
            r'(https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*)',
            r'(https?://[^\s\'"<>\\]+\.mp4[^\s\'"<>\\]*)',
            r'(https?://[^\s\'"<>\\]+playlist[^\s\'"<>\\]*)',
        ]
        for pat in patterns:
            for m in re.findall(pat, html, re.IGNORECASE):
                # undefined 포함된 URL 제외
                if "undefined" not in m and len(m) > 20:
                    return m
        return ""
    except Exception:
        return ""


def get_stream_url(data: dict) -> str:
    if data.get("MOVIE") != "Y":
        return ""
    kind    = data.get("KIND", "")
    cctv_id = data.get("CCTVID", "")
    cctvip  = str(data.get("CCTVIP", ""))
    name    = data.get("CCTVNAME", "")
    if kind in ("KB", "A"):
        return ""
    return _fetch_stream_from_popup(cctv_id, kind, cctvip, name)


# ──────────────────────────────────────────────
# 단일 CCTV 스트림 URL 조회
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
            headers=HEADERS_AJAX, timeout=10
        )
        data = resp.json()
        url  = get_stream_url(data)
        return jsonify({
            "cctvId": cctv_id, "data": data, "streamUrl": url,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# 크롤러
# GET /crawl/start?ids=L904028,L904029,...
# ──────────────────────────────────────────────
def _crawl_worker(cctv_ids):
    global _crawl_status
    _crawl_status.update({
        "running": True, "total": len(cctv_ids),
        "done": 0, "found": 0,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    for cctv_id in cctv_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/map/getCctvInfoById.do",
                params={"cctvId": cctv_id},
                headers=HEADERS_AJAX, timeout=8
            )
            data = resp.json()
            url  = get_stream_url(data)
            if url:
                _stream_cache[cctv_id] = {
                    "url": url, "kind": data.get("KIND", ""),
                    "name": data.get("CCTVNAME", ""),
                    "lat": data.get("YCOORD", 0),
                    "lng": data.get("XCOORD", 0),
                    "center": data.get("CENTERNAME", ""),
                }
                _crawl_status["found"] += 1
        except Exception:
            pass
        _crawl_status["done"] += 1
        time.sleep(0.3)
    _crawl_status["running"] = False


@app.route("/crawl/start")
def crawl_start():
    if _crawl_status["running"]:
        return jsonify({"error": "이미 크롤링 중"}), 400
    ids_str  = request.args.get("ids", "")
    cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    if not cctv_ids:
        return jsonify({"error": "ids 파라미터 필요"}), 400
    t = threading.Thread(target=_crawl_worker, args=(cctv_ids,), daemon=True)
    t.start()
    return jsonify({
        "status": "시작됨", "total": len(cctv_ids),
        "check": "https://nowmoment.onrender.com/crawl/status",
    })


@app.route("/crawl/status")
def crawl_status():
    return jsonify({
        **_crawl_status,
        "cached_count": len(_stream_cache),
        "progress": f"{_crawl_status['done']}/{_crawl_status['total']}",
    })


@app.route("/crawl/result")
def crawl_result():
    return jsonify({"count": len(_stream_cache), "streams": _stream_cache})


# ──────────────────────────────────────────────
# 앱 엔드포인트
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
            R = 6371
            dlat = math.radians(la2 - la1)
            dlng = math.radians(ln2 - ln1)
            a = (math.sin(dlat/2)**2 +
                 math.cos(math.radians(la1)) *
                 math.cos(math.radians(la2)) *
                 math.sin(dlng/2)**2)
            return R * 2 * math.asin(math.sqrt(a))

        nearby = []
        for cid, info in _stream_cache.items():
            d = dist_km(lat, lng, info.get("lat", 0), info.get("lng", 0))
            if d <= radius:
                nearby.append({
                    "cctvId": cid, "name": info["name"],
                    "lat": info["lat"], "lng": info["lng"],
                    "streamUrl": info["url"], "kind": info["kind"],
                    "center": info["center"], "distKm": round(d, 2),
                })
        nearby.sort(key=lambda x: x["distKm"])
        return jsonify({"count": len(nearby), "items": nearby[:20]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
