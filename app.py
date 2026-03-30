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
    "Referer": f"{BASE_URL}/guide/cctvOpenData.do?key={UTIC_KEY}",
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
        "status": "ok", "service": "UTIC CCTV Proxy v11",
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
# KIND별 스트림 URL 내부 API 매핑
# HTML 소스에서 발견한 실제 엔드포인트들
# ──────────────────────────────────────────────
def get_stream_url(data: dict) -> str:
    if data.get("MOVIE") != "Y":
        return ""

    kind    = data.get("KIND", "")
    cctvip  = str(data.get("CCTVIP", ""))
    cctv_id = data.get("CCTVID", "")
    name    = data.get("CCTVNAME", "")

    # KB(KBS 재난포털), A(서울 ActiveX) 스킵
    if kind in ("KB", "A"):
        return ""

    # ── EE/KB: 경기도 교통정보센터 방식 ──────────
    # /map/getGyeonggiCctvUrl.do?cctvIp={cctvip}
    if "EE" in kind or "KB" in kind:
        url = _call_internal_api(
            f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}")
        if url:
            return url

    # ── EEE: ITS 기반 경기도 ──────────────────────
    if "EEE" in kind:
        url = _call_internal_api(
            f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}")
        if url:
            return url

    # ── 기타: 팝업 HTML에서 직접 추출 ─────────────
    return _fetch_from_popup(cctv_id, kind, cctvip, name)


def _call_internal_api(url: str) -> str:
    """UTIC 내부 API 호출 → 스트림 URL 반환"""
    try:
        resp = requests.get(url, headers=HEADERS_AJAX, timeout=8)
        if resp.status_code != 200:
            return ""
        text = resp.text.strip()
        # "null" 또는 빈값이면 스킵
        if not text or text == "null" or text == '"null"':
            return ""
        # JSON 문자열인 경우 파싱
        import json
        try:
            parsed = json.loads(text)
            if isinstance(parsed, str):
                return parsed if parsed.startswith("http") else ""
            if isinstance(parsed, dict):
                for key in ["url", "cctvurl", "streamUrl", "data"]:
                    val = parsed.get(key, "")
                    if val and str(val).startswith("http"):
                        return str(val)
        except Exception:
            # 순수 URL 문자열인 경우
            if text.startswith("http"):
                return text
        return ""
    except Exception:
        return ""


def _fetch_from_popup(cctv_id, kind, cctvip, name="") -> str:
    """팝업 JSP HTML에서 스트림 URL 추출"""
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
        ]
        for pat in patterns:
            for m in re.findall(pat, html, re.IGNORECASE):
                if "undefined" not in m and len(m) > 20:
                    return m
        return ""
    except Exception:
        return ""


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
# 내부 API 직접 테스트
# GET /utic/api?cctvIp=62086
# ──────────────────────────────────────────────
@app.route("/utic/api")
def utic_api():
    cctvip = request.args.get("cctvIp", "62086")
    results = {}

    for endpoint in [
        f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}",
        f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}",
    ]:
        try:
            resp = requests.get(endpoint, headers=HEADERS_AJAX, timeout=8)
            results[endpoint] = {
                "status": resp.status_code,
                "body":   resp.text[:500],
            }
        except Exception as e:
            results[endpoint] = {"error": str(e)}

    return jsonify(results)


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
