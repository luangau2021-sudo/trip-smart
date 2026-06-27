# core/goong_speed_limit.py
# Goong Speed Limit Layer cho TripSmart/RANav-X
# Chỉ lấy dữ liệu tốc độ tối đa từ Goong Speed Limit API, không tự suy đoán tốc độ.

from __future__ import annotations

import os
import time
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None


GOONG_SPEED_URL = "https://speed-api.goong.io/roadinfo"
_CACHE_TTL_SEC = 900
_POINT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_ROUTE_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _read_env_file_key(name: str = "GOONG_API_KEY") -> str:
    """Đọc .env thủ công để không cần bắt buộc cài python-dotenv."""
    candidates = []
    try:
        cwd = Path.cwd()
        candidates += [cwd / ".env", cwd.parent / ".env"]
    except Exception:
        pass

    try:
        here = Path(__file__).resolve()
        candidates += [here.parent.parent / ".env", here.parent / ".env"]
    except Exception:
        pass

    seen = set()
    for path in candidates:
        try:
            if not path or path in seen or not path.exists():
                continue
            seen.add(path)
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def get_goong_key() -> str:
    """Ưu tiên Streamlit Secrets, sau đó biến môi trường, cuối cùng là file .env."""
    if st is not None:
        try:
            key = st.secrets.get("GOONG_API_KEY", "")
            if key:
                return str(key).strip()
        except Exception:
            pass

    key = os.getenv("GOONG_API_KEY", "")
    if key:
        return str(key).strip()

    return _read_env_file_key("GOONG_API_KEY")


def _hav_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p = math.pi / 180.0
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.asin(math.sqrt(max(0.0, a)))


def _route_cumulative_km(polyline: List[List[float]]) -> List[float]:
    out = [0.0]
    total = 0.0
    for a, b in zip(polyline[:-1], polyline[1:]):
        try:
            total += _hav_km(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
        except Exception:
            pass
        out.append(total)
    return out


def _sample_route_points_with_km(
    polyline: List[List[float]],
    every_km: float = 1.5,
    max_points: int = 180,
) -> List[Tuple[float, float, float]]:
    """Trả về [(lat, lon, route_km)] từ polyline dạng [lon, lat]."""
    if not polyline:
        return []

    cum = _route_cumulative_km(polyline)
    total = cum[-1] if cum else 0.0
    if total <= 0:
        p = polyline[0]
        return [(float(p[1]), float(p[0]), 0.0)]

    step = max(0.5, float(every_km or 1.5))
    targets = [0.0]
    t = step
    while t < total:
        targets.append(t)
        t += step
    targets.append(total)

    # Route rất dài: giữ số request trong giới hạn an toàn.
    max_points = max(2, int(max_points or 180))
    if len(targets) > max_points:
        targets = [total * i / max(1, max_points - 1) for i in range(max_points)]

    pts: List[Tuple[float, float, float]] = []
    j = 0
    for target in targets:
        while j + 1 < len(cum) and cum[j + 1] < target:
            j += 1

        if j + 1 >= len(polyline):
            p = polyline[-1]
            pts.append((float(p[1]), float(p[0]), float(total)))
            continue

        a, b = polyline[j], polyline[j + 1]
        seg = max(1e-9, float(cum[j + 1]) - float(cum[j]))
        ratio = (float(target) - float(cum[j])) / seg
        lon = float(a[0]) + (float(b[0]) - float(a[0])) * ratio
        lat = float(a[1]) + (float(b[1]) - float(a[1])) * ratio
        pts.append((lat, lon, float(target)))

    uniq: List[Tuple[float, float, float]] = []
    seen = set()
    for lat, lon, km in pts:
        key = (round(lat, 5), round(lon, 5), round(km, 2))
        if key not in seen:
            seen.add(key)
            uniq.append((lat, lon, km))
    return uniq


def _parse_speed_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        text = str(value).strip().lower()
        if not text or text in {"none", "null", "unknown", "variable"}:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if not m:
            return None
        v = float(m.group(1))
    if v <= 0 or v > 180:
        return None
    return int(round(v))


def _extract_speed_from_payload(payload: Any) -> Optional[int]:
    """Nhận nhiều format trả về có thể gặp: speed, maxspeed, speed_limit..."""
    if not isinstance(payload, dict):
        return None

    direct_keys = (
        "speed",
        "maxspeed",
        "max_speed",
        "speed_limit",
        "speedLimit",
        "maxSpeedLimitInKmh",
        "max_speed_limit_kmh",
    )
    for key in direct_keys:
        spd = _parse_speed_value(payload.get(key))
        if spd is not None:
            return spd

    # Một số API có thể bọc dữ liệu trong data/result/road.
    for key in ("data", "result", "road", "road_info", "roadInfo"):
        child = payload.get(key)
        if isinstance(child, dict):
            spd = _extract_speed_from_payload(child)
            if spd is not None:
                return spd
        elif isinstance(child, list):
            for item in child:
                spd = _extract_speed_from_payload(item)
                if spd is not None:
                    return spd
    return None


def _extract_road_name(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("road_name", "roadName", "name", "street", "street_name"):
        val = payload.get(key)
        if val:
            return str(val)

    for key in ("data", "result", "road", "road_info", "roadInfo"):
        child = payload.get(key)
        if isinstance(child, dict):
            val = _extract_road_name(child)
            if val:
                return val
        elif isinstance(child, list):
            for item in child:
                val = _extract_road_name(item)
                if val:
                    return val
    return ""


def get_speed_limit_from_goong(lat: float, lon: float, api_key: str | None = None) -> Dict[str, Any]:
    """Lấy tốc độ tối đa tại 1 điểm bằng Goong Speed Limit API."""
    if requests is None:
        return {
            "ok": False,
            "speed_limit": None,
            "unit": "km/h",
            "source": "goong_speed_limit",
            "message": "Thiếu thư viện requests",
            "raw": None,
        }

    key = (api_key or get_goong_key() or "").strip()
    if not key:
        return {
            "ok": False,
            "speed_limit": None,
            "unit": "km/h",
            "source": "goong_speed_limit",
            "message": "Thiếu GOONG_API_KEY",
            "raw": None,
        }

    lat = float(lat)
    lon = float(lon)
    cache_key = f"{round(lat, 5)},{round(lon, 5)}"
    now = time.time()

    cached = _POINT_CACHE.get(cache_key)
    if cached and now - cached[0] <= _CACHE_TTL_SEC:
        return dict(cached[1])

    try:
        res = requests.get(
            GOONG_SPEED_URL,
            params={"lat": lat, "lon": lon, "api_key": key},
            timeout=7,
        )
        res.raise_for_status()
        payload = res.json()
    except Exception as e:
        data = {
            "ok": False,
            "speed_limit": None,
            "unit": "km/h",
            "source": "goong_speed_limit",
            "message": f"Lỗi gọi Goong: {e}",
            "raw": None,
        }
        _POINT_CACHE[cache_key] = (now, data)
        return dict(data)

    speed = _extract_speed_from_payload(payload)
    road_name = _extract_road_name(payload)

    if speed is None:
        data = {
            "ok": False,
            "speed_limit": None,
            "unit": "km/h",
            "source": "goong_speed_limit",
            "road_name": road_name,
            "message": "Goong không có dữ liệu tốc độ tối đa tại điểm này",
            "raw": payload,
        }
    else:
        data = {
            "ok": True,
            "speed_limit": int(speed),
            "unit": "km/h",
            "source": "goong_speed_limit",
            "road_name": road_name,
            "message": "OK",
            "raw": payload,
        }

    _POINT_CACHE[cache_key] = (now, data)
    return dict(data)


def _route_cache_key(polyline: List[List[float]], mode: str | None, every_km: float, max_points: int) -> str:
    if not polyline:
        return "empty"
    pts = []
    for p in (polyline[:3] + polyline[-3:]):
        try:
            pts.append(f"{float(p[1]):.4f},{float(p[0]):.4f}")
        except Exception:
            pass
    pts.append(str(len(polyline)))
    pts.append(str(mode or "car"))
    pts.append(str(every_km))
    pts.append(str(max_points))
    return "|".join(pts)


def get_goong_speed_limits_on_route(
    polyline: List[List[float]],
    mode: str | None = "car",
    sample_every_km: float = 1.5,
    max_points: int = 180,
    max_workers: int = 8,
) -> List[Dict[str, Any]]:
    """
    Lấy speed limit dọc tuyến bằng cách gọi Goong Speed Limit tại nhiều điểm mẫu.
    Trả về format tương thích ui.streamlit_map.make_full_map(speed_segments=...).
    """
    if not polyline or len(polyline) < 2:
        return []

    key = _route_cache_key(polyline, mode, sample_every_km, max_points)
    now = time.time()
    cached = _ROUTE_CACHE.get(key)
    if cached and now - cached[0] <= _CACHE_TTL_SEC:
        return list(cached[1])

    api_key = get_goong_key()
    if not api_key:
        return []

    samples = _sample_route_points_with_km(
        polyline,
        every_km=sample_every_km,
        max_points=max_points,
    )
    if not samples:
        return []

    def _fetch(item: Tuple[float, float, float]) -> Optional[Dict[str, Any]]:
        lat, lon, route_km = item
        data = get_speed_limit_from_goong(lat, lon, api_key=api_key)
        if not data or not data.get("ok") or data.get("speed_limit") is None:
            return None
        speed = _parse_speed_value(data.get("speed_limit"))
        if speed is None:
            return None
        road_name = data.get("road_name") or ""
        return {
            "lat": float(lat),
            "lon": float(lon),
            "route_km": round(float(route_km), 3),
            "maxspeed": int(speed),
            "maxspeed_text": f"{int(speed)} km/h",
            "mode": str(mode or "car"),
            "source": "goong_speed_limit",
            "source_tag": "goong_speed",
            "raw_maxspeed": data.get("speed_limit"),
            "highway": road_name,
            "name": road_name,
            "road_name": road_name,
            "distance_from_route_m": 0.0,
        }

    results: List[Dict[str, Any]] = []
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = max(1, min(int(max_workers or 8), 12, len(samples)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_fetch, item) for item in samples]
            for fut in as_completed(futures):
                item = fut.result()
                if item:
                    results.append(item)
    except Exception:
        # Fallback tuần tự nếu môi trường không cho chạy thread.
        for item in samples:
            got = _fetch(item)
            if got:
                results.append(got)

    results.sort(key=lambda x: float(x.get("route_km", 0) or 0))

    # Giữ nhiều điểm để JS hiển thị theo GPS không bị khoảng trắng giữa đường.
    # Chỉ bỏ trùng gần như cùng vị trí và cùng tốc độ.
    compact: List[Dict[str, Any]] = []
    for item in results:
        if compact:
            prev = compact[-1]
            if (
                abs(float(item["route_km"]) - float(prev.get("route_km", 0))) < 0.08
                and item.get("maxspeed") == prev.get("maxspeed")
            ):
                continue
        compact.append(item)

    _ROUTE_CACHE[key] = (now, compact)
    return list(compact)
