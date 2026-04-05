from flask import Flask, jsonify, request
import requests
import re
import threading
import time
import math
import json
import csv
import os

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

# ── 파일 경로 ─────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "stream_cache.json")
CSV_FILE   = os.path.join(BASE_DIR, "OpenDataCCTV.csv")

# ── 전역 상태 ─────────────────────────────────
_stream_cache = {}
_crawl_status = {
    "running":      False,
    "total":        0,
    "done":         0,
    "found":        0,
    "started_at":   None,
    "finished_at":  None,
    "batch":        "",
}

# ──────────────────────────────────────────────
# 캐시 파일 저장/로드
# ──────────────────────────────────────────────
def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_stream_cache, f, ensure_ascii=False)
        print(f"💾 캐시 저장: {len(_stream_cache)}개")
    except Exception as e:
        print(f"❌ 캐시 저장 실패: {e}")

def load_cache():
    global _stream_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _stream_cache = json.load(f)
            print(f"✅ 캐시 로드: {len(_stream_cache)}개")
        except Exception as e:
            print(f"❌ 캐시 로드 실패: {e}")
            _stream_cache = {}

# 서버 시작 시 캐시 로드
load_cache()

# ──────────────────────────────────────────────
# CSV에서 CCTV ID 로드
# ──────────────────────────────────────────────
def load_cctv_ids() -> list:
    if not os.path.exists(CSV_FILE):
        print(f"⚠️ CSV 없음: {CSV_FILE}")
        return []
    ids = []
    try:
        with open(CSV_FILE, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if len(row) >= 2 and row[1].strip():
                    ids.append(row[1].strip())
        print(f"✅ CSV 로드: {len(ids)}개")
    except Exception as e:
        print(f"❌ CSV 로드 실패: {e}")
    return ids

# ──────────────────────────────────────────────
# URL 정규화
# ──────────────────────────────────────────────
def _normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url or url in ("null", '"null"'):
        return ""
    if url.startswith("//"):
        url = "http:" + url
    if not url.startswith("http"):
        return ""
    return url

def _call_internal_api(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS_AJAX, timeout=8)
        if resp.status_code != 200:
            return ""
        text = resp.text.strip()
        if not text or text in ("null", '"null"'):
            return ""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, str):
                return _normalize_url(parsed)
            if isinstance(parsed, dict):
                for key in ["url", "cctvurl", "streamUrl", "data"]:
                    n = _normalize_url(str(parsed.get(key, "")))
                    if n:
                        return n
        except Exception:
            pass
        return _normalize_url(text)
    except Exception:
        return ""

def get_stream_url(data: dict) -> str:
    if data.get("MOVIE") != "Y":
        return ""
    kind   = data.get("KIND", "")
    cctvip = str(data.get("CCTVIP", ""))
    cctv_id = data.get("CCTVID", "")
    name   = data.get("CCTVNAME", "")
    if kind in ("KB", "A"):
        return ""
    if "EE" in kind:
        ep = (f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}"
              if kind == "EEE"
              else f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}")
        url = _call_internal_api(ep)
        if url:
            return url
    return _fetch_from_popup(cctv_id, kind, cctvip, name)

def _fetch_from_popup(cctv_id, kind, cctvip, name="") -> str:
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
        for pat in [
            r'(https?://cctvsec\.ktict\.co\.kr[^\s\'"<>\\]+)',
            r'(//cctvsec\.ktict\.co\.kr[^\s\'"<>\\]+)',
            r'(https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*)',
            r'(https?://[^\s\'"<>\\]+\.mp4[^\s\'"<>\\]*)',
        ]:
            for m in re.findall(pat, html, re.IGNORECASE):
                n = _normalize_url(m)
                if n and "undefined" not in n:
                    return n
        return ""
    except Exception:
        return ""

# ──────────────────────────────────────────────
# 배치 크롤링 워커
# 500개씩 나눠서 처리, 각 배치 완료 시 파일 저장
# ──────────────────────────────────────────────
BATCH_SIZE = 500

def _crawl_worker(cctv_ids: list):
    global _crawl_status

    _crawl_status.update({
        "running":     True,
        "total":       len(cctv_ids),
        "done":        0,
        "found":       len(_stream_cache),  # 기존 캐시 포함
        "started_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None,
        "batch":       "",
    })

    done = 0
    for i in range(0, len(cctv_ids), BATCH_SIZE):
        batch     = cctv_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(cctv_ids) + BATCH_SIZE - 1) // BATCH_SIZE
        _crawl_status["batch"] = f"{batch_num}/{total_batches}"
        print(f"🔄 배치 {batch_num}/{total_batches} 시작 ({len(batch)}개)")

        for cctv_id in batch:
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

            done += 1
            _crawl_status["done"] = done
            time.sleep(0.15)

        # 배치 완료 시마다 파일 저장 (서버 꺼져도 유지)
        save_cache()
        print(f"✅ 배치 {batch_num} 완료, 누적 {len(_stream_cache)}개 저장")

    _crawl_status.update({
        "running":     False,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "batch":       "완료",
    })
    print(f"🎉 전체 크롤링 완료: {len(_stream_cache)}개")


@app.route("/")
def health():
    return jsonify({
        "status":  "ok",
        "service": "UTIC CCTV Proxy v14",
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

@app.route("/crawl/start")
def crawl_start():
    if _crawl_status["running"]:
        return jsonify({"error": "이미 크롤링 중", "status": _crawl_status}), 400

    ids_str  = request.args.get("ids", "")
    if ids_str:
        cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    else:
        cctv_ids = load_cctv_ids()

    if not cctv_ids:
        return jsonify({"error": "ids 없음"}), 400

    t = threading.Thread(target=_crawl_worker, args=(cctv_ids,), daemon=True)
    t.start()

    return jsonify({
        "status":     "시작됨",
        "total":      len(cctv_ids),
        "batch_size": BATCH_SIZE,
        "note":       "500개마다 파일 저장 → 서버 재시작 시 복원됨",
        "check":      "https://nowmoment.onrender.com/crawl/status",
    })

@app.route("/crawl/status")
def crawl_status():
    return jsonify({
        **_crawl_status,
        "cached_count": len(_stream_cache),
        "progress":     f"{_crawl_status['done']}/{_crawl_status['total']}",
    })

@app.route("/crawl/result")
def crawl_result():
    return jsonify({"count": len(_stream_cache), "streams": _stream_cache})

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
        return jsonify({"cctvId": cctv_id, "data": data, "streamUrl": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/utic/api")
def utic_api():
    cctvip = request.args.get("cctvIp", "62086")
    results = {}
    for ep in [
        f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}",
        f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}",
    ]:
        try:
            resp = requests.get(ep, headers=HEADERS_AJAX, timeout=8)
            raw  = resp.text.strip()
            results[ep] = {
                "status":     resp.status_code,
                "body":       raw[:300],
                "normalized": _normalize_url(raw),
            }
        except Exception as e:
            results[ep] = {"error": str(e)}
    return jsonify(results)

# ──────────────────────────────────────────────
# 앱 메인 엔드포인트
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
                    "cctvId":    cid,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
