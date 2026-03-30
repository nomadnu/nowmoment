from flask import Flask, jsonify, request
import requests
import re
import threading
import time
import math
import json
import csv
import os

from apscheduler.schedulers.background import BackgroundScheduler

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

# ── 전역 상태 ──────────────────────────────────
_stream_cache = {}          # { cctvId: {url, kind, name, lat, lng, center} }
_crawl_lock   = threading.Lock()
_crawl_status = {
    "running":      False,
    "total":        0,
    "done":         0,
    "found":        0,
    "started_at":   None,
    "finished_at":  None,
    "next_refresh": None,
}

# ──────────────────────────────────────────────
# CSV에서 CCTV ID 목록 로드
# GitHub 저장소에 OpenDataCCTV.csv 파일 필요
# ──────────────────────────────────────────────
def load_cctv_ids_from_csv() -> list:
    csv_path = os.path.join(os.path.dirname(__file__), "OpenDataCCTV.csv")
    if not os.path.exists(csv_path):
        print(f"⚠️ CSV 파일 없음: {csv_path}")
        return []

    ids = []
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 스킵
            for row in reader:
                if len(row) >= 2 and row[1].strip():
                    ids.append(row[1].strip())
        print(f"✅ CSV 로드 완료: {len(ids)}개 CCTV ID")
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


# ──────────────────────────────────────────────
# UTIC 내부 API 호출
# ──────────────────────────────────────────────
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
                    val = str(parsed.get(key, ""))
                    n = _normalize_url(val)
                    if n:
                        return n
        except Exception:
            pass
        return _normalize_url(text)
    except Exception:
        return ""


# ──────────────────────────────────────────────
# KIND별 스트림 URL 조회
# ──────────────────────────────────────────────
def get_stream_url(data: dict) -> str:
    if data.get("MOVIE") != "Y":
        return ""

    kind    = data.get("KIND", "")
    cctvip  = str(data.get("CCTVIP", ""))
    cctv_id = data.get("CCTVID", "")
    name    = data.get("CCTVNAME", "")

    if kind in ("KB", "A"):
        return ""

    # EE 계열: 경기도 교통정보센터 방식
    if "EE" in kind:
        endpoint = (
            f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}"
            if kind == "EEE"
            else f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}"
        )
        url = _call_internal_api(endpoint)
        if url:
            return url

    # 기타: 팝업 HTML에서 추출
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
        patterns = [
            r'(https?://cctvsec\.ktict\.co\.kr[^\s\'"<>\\]+)',
            r'(//cctvsec\.ktict\.co\.kr[^\s\'"<>\\]+)',
            r'(https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*)',
            r'(https?://[^\s\'"<>\\]+\.mp4[^\s\'"<>\\]*)',
        ]
        for pat in patterns:
            for m in re.findall(pat, html, re.IGNORECASE):
                n = _normalize_url(m)
                if n and "undefined" not in n:
                    return n
        return ""
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 크롤링 워커 (백그라운드)
# ──────────────────────────────────────────────
def _crawl_worker(cctv_ids: list):
    global _stream_cache, _crawl_status

    with _crawl_lock:
        if _crawl_status["running"]:
            return

        _crawl_status.update({
            "running":     True,
            "total":       len(cctv_ids),
            "done":        0,
            "found":       0,
            "started_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
        })

    new_cache = {}
    done  = 0
    found = 0

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
                new_cache[cctv_id] = {
                    "url":    url,
                    "kind":   data.get("KIND", ""),
                    "name":   data.get("CCTVNAME", ""),
                    "lat":    data.get("YCOORD", 0),
                    "lng":    data.get("XCOORD", 0),
                    "center": data.get("CENTERNAME", ""),
                }
                found += 1
        except Exception:
            pass

        done += 1
        _crawl_status["done"]  = done
        _crawl_status["found"] = found
        time.sleep(0.2)  # 서버 부하 방지

    # 캐시 갱신
    _stream_cache = new_cache

    next_refresh = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() + 7200)
    )
    _crawl_status.update({
        "running":      False,
        "finished_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
        "next_refresh": next_refresh,
    })
    print(f"✅ 크롤링 완료: {found}/{done}개 URL 확보, 다음 갱신: {next_refresh}")


def start_crawl_async(cctv_ids: list):
    t = threading.Thread(target=_crawl_worker, args=(cctv_ids,), daemon=True)
    t.start()


# ──────────────────────────────────────────────
# 2시간마다 자동 갱신 스케줄러
# ──────────────────────────────────────────────
def scheduled_refresh():
    print("🔄 스케줄 갱신 시작...")
    ids = load_cctv_ids_from_csv()
    if ids:
        start_crawl_async(ids)
    else:
        print("⚠️ CSV 없음 - 갱신 스킵")


scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_refresh, "interval", hours=2, id="refresh")
scheduler.start()

# 앱 시작 시 초기 크롤링
def initial_crawl():
    time.sleep(3)  # 서버 완전 기동 후 시작
    ids = load_cctv_ids_from_csv()
    if ids:
        print(f"🚀 초기 크롤링 시작: {len(ids)}개")
        start_crawl_async(ids)
    else:
        print("⚠️ CSV 없음 - 초기 크롤링 스킵")

threading.Thread(target=initial_crawl, daemon=True).start()


# ──────────────────────────────────────────────
# API 엔드포인트
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({
        "status":  "ok",
        "service": "UTIC CCTV Proxy v13",
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

@app.route("/crawl/status")
def crawl_status():
    return jsonify({
        **_crawl_status,
        "cached_count": len(_stream_cache),
        "progress":     f"{_crawl_status['done']}/{_crawl_status['total']}",
    })

@app.route("/crawl/start")
def crawl_start():
    if _crawl_status["running"]:
        return jsonify({"error": "이미 크롤링 중", "status": _crawl_status}), 400
    ids_str  = request.args.get("ids", "")
    if ids_str:
        cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    else:
        cctv_ids = load_cctv_ids_from_csv()
    if not cctv_ids:
        return jsonify({"error": "ids 없음 (CSV 파일 또는 ids 파라미터 필요)"}), 400
    start_crawl_async(cctv_ids)
    return jsonify({
        "status": "시작됨",
        "total":  len(cctv_ids),
        "check":  "https://nowmoment.onrender.com/crawl/status",
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
    cctvip  = request.args.get("cctvIp", "62086")
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
