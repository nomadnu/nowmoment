from flask import Flask, jsonify, request
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
    "Referer": f"{BASE_URL}/guide/cctvOpenData.do?key={UTIC_KEY}",
    "Accept": "text/html,application/xhtml+xml,*/*",
}
HEADERS_AJAX = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ──────────────────────────────────────────────
# URL 정규화: // → http://, 공백 제거
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
                    n = _normalize_url(str(parsed.get(key, "")))
                    if n:
                        return n
        except Exception:
            pass
        return _normalize_url(text)
    except Exception:
        return ""

# ──────────────────────────────────────────────
# 팝업 HTML에서 스트림 URL 추출
# ──────────────────────────────────────────────
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
# KIND별 스트림 URL 조회
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

    # EE, EEE, AC 계열: getGyeonggiCctvUrl.do 방식
    if "EE" in kind or kind == "AC":
        ep = (f"{BASE_URL}/map/getGyeonggiCctvUrlFromIts.do?cctvIp={cctvip}"
              if kind == "EEE"
              else f"{BASE_URL}/map/getGyeonggiCctvUrl.do?cctvIp={cctvip}")
        url = _call_internal_api(ep)
        if url:
            return url

    # 기타: 팝업 HTML에서 직접 추출
    return _fetch_from_popup(cctv_id, kind, cctvip, name)


# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "UTIC CCTV Proxy v17"})

@app.route("/myip")
def myip():
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 단일 CCTV 스트림 URL 조회
# GET /stream?cctvId=L904028
# ──────────────────────────────────────────────
@app.route("/stream")
def stream():
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
            "cctvId":    cctv_id,
            "name":      data.get("CCTVNAME", ""),
            "streamUrl": url,
            "kind":      data.get("KIND", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 여러 CCTV ID 한꺼번에 조회 (최대 10개)
# GET /streams?ids=L904028,L904029,L904030
# ──────────────────────────────────────────────
@app.route("/streams")
def streams():
    ids_str  = request.args.get("ids", "")
    cctv_ids = [i.strip() for i in ids_str.split(",") if i.strip()][:10]
    if not cctv_ids:
        return jsonify({"error": "ids 파라미터 필요"}), 400

    results = []
    for cctv_id in cctv_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/map/getCctvInfoById.do",
                params={"cctvId": cctv_id},
                headers=HEADERS_AJAX, timeout=8
            )
            data = resp.json()
            url  = get_stream_url(data)
            results.append({
                "cctvId":    cctv_id,
                "name":      data.get("CCTVNAME", ""),
                "streamUrl": url,
                "kind":      data.get("KIND", ""),
            })
        except Exception as e:
            results.append({"cctvId": cctv_id, "error": str(e)})

    return jsonify({"count": len(results), "items": results})

# ──────────────────────────────────────────────
# 단일 CCTV 상세 정보 + 스트림 URL (디버깅)
# GET /utic/info?cctvId=E620016
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
            "cctvId":    cctv_id,
            "data":      data,
            "streamUrl": url,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# 내부 API 직접 테스트 (디버깅)
# GET /utic/api?cctvIp=3011645
# ──────────────────────────────────────────────
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
# 팝업 HTML 분석 (디버깅)
# GET /utic/popup?cctvId=E620016
# ──────────────────────────────────────────────
@app.route("/utic/popup")
def utic_popup():
    cctv_id = request.args.get("cctvId", "")
    if not cctv_id:
        return jsonify({"error": "cctvId 파라미터 필요"}), 400
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

        all_urls = re.findall(r'(https?://[^\s\'"<>\\]+)', html)
        js_vars  = re.findall(r'var\s+(\w+)\s*=\s*["\']([^"\']+)["\']', html)

        return jsonify({
            "cctvId":       cctv_id,
            "kind":         kind,
            "cctvip":       cctvip,
            "popup_url":    popup_url,
            "status":       popup_resp.status_code,
            "all_urls":     list(set(all_urls))[:20],
            "js_vars":      js_vars[:20],
            "html_preview": html[:2000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
