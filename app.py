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

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v6"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# CCTV ID로 상세 정보 조회
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
        try:
            data = resp.json()
        except Exception:
            data = None
        return jsonify({
            "status":       resp.status_code,
            "content_type": resp.headers.get("Content-Type", ""),
            "data":         data,
            "raw":          resp.text[:500] if not data else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 여러 CCTV ID 한꺼번에 조회
# GET /utic/batch?ids=L904028,L904029,L933092
# ──────────────────────────────────────────────
@app.route("/utic/batch")
def utic_batch():
    ids_str = request.args.get("ids", "")
    if not ids_str:
        return jsonify({"error": "ids 파라미터 필요 (쉼표 구분)"}), 400

    cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    results  = []

    for cctv_id in cctv_ids[:20]:  # 최대 20개
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
                data = {"raw": resp.text[:200]}
            results.append({"cctvId": cctv_id, "data": data})
        except Exception as e:
            results.append({"cctvId": cctv_id, "error": str(e)})

    return jsonify(results)

# ──────────────────────────────────────────────
# CCTV ID → 스트림 URL 생성
# CCTVIP와 스트림 서버 주소로 HLS URL 조합
# GET /utic/stream?cctvId=L904028
# ──────────────────────────────────────────────
@app.route("/utic/stream")
def utic_stream():
    cctv_id = request.args.get("cctvId", "")
    if not cctv_id:
        return jsonify({"error": "cctvId 파라미터 필요"}), 400

    try:
        # CCTV 정보 조회
        resp = requests.get(
            f"{BASE_URL}/map/getCctvInfoById.do",
            params={"cctvId": cctv_id},
            headers=HEADERS,
            timeout=10
        )
        data = resp.json()

        cctvip   = str(data.get("CCTVIP", ""))
        kind     = data.get("KIND", "")
        strm_id  = data.get("STRMID", cctv_id)
        movie    = data.get("MOVIE", "N")

        # 확인된 스트림 URL 패턴
        # http://637bef0325205.streamlock.net/live/cctv20.stream/playlist.m3u8
        # CCTVIP = 숫자 → cctv{숫자}.stream 패턴 가능성
        stream_url = None

        if movie == "Y" and cctvip:
            # 패턴 1: streamlock.net (강릉시 확인됨)
            candidate = (f"http://637bef0325205.streamlock.net/live/"
                        f"cctv{cctvip}.stream/playlist.m3u8")

            # HEAD 요청으로 URL 유효성 확인
            try:
                check = requests.head(candidate, timeout=5)
                if check.status_code == 200:
                    stream_url = candidate
            except Exception:
                pass

        return jsonify({
            "cctvId":    cctv_id,
            "kind":      kind,
            "cctvip":    cctvip,
            "strmId":    strm_id,
            "movie":     movie,
            "streamUrl": stream_url,
            "candidate": (f"http://637bef0325205.streamlock.net/live/"
                         f"cctv{cctvip}.stream/playlist.m3u8") if cctvip else None,
            "rawData":   data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

        return jsonify({
            "status":  "준비중",
            "message": "스트림 URL 패턴 확인 후 구현 예정",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
