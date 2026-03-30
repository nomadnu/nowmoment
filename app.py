from flask import Flask, jsonify, request, Response
import requests
import re
import json
import threading
import time
import os

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

# ── 수집된 스트림 URL 캐시 (메모리) ──────────────
# { cctvId: { url, kind, name, lat, lng } }
_stream_cache = {}
_crawl_status = {
    "running":   False,
    "total":     0,
    "done":      0,
    "found":     0,
    "started_at": None,
}

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({
        "status":  "ok",
        "service": "UTIC CCTV Proxy v7",
        "cached":  len(_stream_cache),
    })

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# CCTV ID → 스트림 URL 추출
# KIND별로 스트림 URL 패턴 적용
# ──────────────────────────────────────────────
def get_stream_url(cctv_data: dict) -> str:
    kind    = cctv_data.get("KIND", "")
    cctvip  = str(cctv_data.get("CCTVIP", ""))
    strm_id = cctv_data.get("STRMID", "")
    cctv_id = cctv_data.get("CCTVID", "")
    movie   = cctv_data.get("MOVIE", "N")
    center  = cctv_data.get("CENTERNAME", "")

    if movie != "Y":
        return ""

    # ── 지역별/KIND별 스트림 URL 패턴 ──────────────
    # 새로운 패턴 발견 시 여기에 추가

    # 서울 (KIND=A: smartway.seoul.go.kr ASX → HLS 변환 불가)
    if kind == "A":
        return ""

    # 강릉시 (KIND=EE: streamlock.net HLS)
    # 확인: cctv20.stream → CCTVIP 62086 (매핑 필요)
    # → 팝업 HTML에서 직접 추출 시도
    if kind == "EE" or "강릉" in center:
        return _fetch_popup_stream(cctv_id)

    # 국도/고속도로 (ITS API로 처리 → 여기선 빈값)
    if kind in ("N", "H"):
        return ""

    # KBS 재난포털 (KIND=KB: 전용 플레이어)
    if kind == "KB":
        return ""

    # 기타 → 팝업 HTML에서 HLS URL 추출 시도
    url = _fetch_popup_stream(cctv_id)
    return url


def _fetch_popup_stream(cctv_id: str) -> str:
    """팝업 페이지 HTML에서 m3u8/rtmp URL 추출"""
    try:
        # UTIC 팝업 페이지 직접 요청
        popup_endpoints = [
            f"{BASE_URL}/map/openDataCctvStream.do?cctvId={cctv_id}&key={UTIC_KEY}",
            f"{BASE_URL}/map/cctvStream.do?cctvId={cctv_id}",
        ]

        for ep in popup_endpoints:
            try:
                resp = requests.get(
                    ep,
                    headers={**HEADERS, "Accept": "text/html"},
                    timeout=8
                )
                if resp.status_code != 200:
                    continue

                html = resp.text

                # HLS URL 패턴 찾기
                patterns = [
                    r'(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)',
                    r'(https?://[^\s\'"<>]+playlist\.m3u8[^\s\'"<>]*)',
                    r'file[=:]\s*["\']?(https?://[^\s\'"<>&]+)',
                    r'src[=:]\s*["\']?(https?://[^\s\'"<>&]+\.m3u8)',
                    r'streamer[=:]["\']?(rtmp://[^\s\'"<>&]+)',
                ]

                for pat in patterns:
                    matches = re.findall(pat, html, re.IGNORECASE)
                    if matches:
                        # rtmp는 모바일 미지원
                        for m in matches:
                            if m.startswith("http") and (
                                "m3u8" in m or "stream" in m):
                                return m
            except Exception:
                continue

        return ""
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 크롤러 실행 (백그라운드 스레드)
# CCTV ID 목록을 순회하며 스트림 URL 수집
# ──────────────────────────────────────────────
def _crawl_worker(cctv_ids: list):
    global _stream_cache, _crawl_status

    _crawl_status["running"]    = True
    _crawl_status["total"]      = len(cctv_ids)
    _crawl_status["done"]       = 0
    _crawl_status["found"]      = 0
    _crawl_status["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    for cctv_id in cctv_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/map/getCctvInfoById.do",
                params={"cctvId": cctv_id},
                headers=HEADERS,
                timeout=8
            )
            data = resp.json()

            if data.get("MOVIE") == "Y":
                url = get_stream_url(data)
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
        # 서버 부하 방지 (0.2초 간격)
        time.sleep(0.2)

    _crawl_status["running"] = False


# ──────────────────────────────────────────────
# 크롤러 시작
# POST /crawl/start  body: {"ids": ["L904028", ...]}
# ──────────────────────────────────────────────
@app.route("/crawl/start", methods=["POST"])
def crawl_start():
    if _crawl_status["running"]:
        return jsonify({"error": "이미 크롤링 중"}), 400

    body     = request.get_json(force=True, silent=True) or {}
    cctv_ids = body.get("ids", [])

    if not cctv_ids:
        return jsonify({"error": "ids 목록 필요"}), 400

    t = threading.Thread(
        target=_crawl_worker,
        args=(cctv_ids,),
        daemon=True
    )
    t.start()

    return jsonify({
        "status":  "started",
        "total":   len(cctv_ids),
        "message": "/crawl/status 에서 진행상황 확인",
    })

# ──────────────────────────────────────────────
# 크롤링 진행 상황
# GET /crawl/status
# ──────────────────────────────────────────────
@app.route("/crawl/status")
def crawl_status():
    return jsonify({
        **_crawl_status,
        "cached_count": len(_stream_cache),
    })

# ──────────────────────────────────────────────
# 수집된 스트림 URL 전체 조회
# GET /crawl/result
# ──────────────────────────────────────────────
@app.route("/crawl/result")
def crawl_result():
    return jsonify({
        "count":   len(_stream_cache),
        "streams": _stream_cache,
    })

# ──────────────────────────────────────────────
# 앱에서 사용할 최종 엔드포인트
# 위경도 기반 근처 CCTV + 스트림 URL 반환
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

        import math
        def dist_km(lat1, lng1, lat2, lng2):
            R = 6371
            dlat = math.radians(lat2 - lat1)
            dlng = math.radians(lng2 - lng1)
            a = (math.sin(dlat/2)**2 +
                 math.cos(math.radians(lat1)) *
                 math.cos(math.radians(lat2)) *
                 math.sin(dlng/2)**2)
            return R * 2 * math.asin(math.sqrt(a))

        nearby = []
        for cctv_id, info in _stream_cache.items():
            d = dist_km(lat, lng,
                        info.get("lat", 0),
                        info.get("lng", 0))
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
        return jsonify({
            "count": len(nearby),
            "items": nearby[:20],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
            headers=HEADERS,
            timeout=10
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
