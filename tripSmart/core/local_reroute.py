# core/local_reroute.py
# Đổi tuyến cục bộ quanh điểm đỏ: chỉ thay đoạn trong bán kính 10km quanh điểm đỏ.

import math
from datetime import datetime, timedelta

import streamlit as st

from core.route_state import _persist_current_route_snapshot
from core.route_calc import (
    RED_RISK_THRESHOLD,
    _haversine_km,
    _distance_km_from_polyline,
    _get_route_distance_km,
    _apply_avg_speed_timing,
    _format_duration_from_seconds,
    _get_route_duration_seconds,
    _compute_route_forecast,
    _clear_route_view_cache,
)
from core.copilot import _copilot_segment_score, _route_avg_risk_for_copilot
from core.legal_route_filter import validate_route_for_mode, is_reasonable_detour, detour_limit_text

def _coord_at_route_km(polyline, target_km):
    """Quy đổi route_km của AI Forecast thành tọa độ thật trên polyline [lon, lat]."""
    try:
        if not polyline or target_km is None:
            return None, None
        target = max(0.0, float(target_km))
        acc = 0.0
        prev = polyline[0]
        if target <= 0:
            return float(prev[1]), float(prev[0])
        for cur in polyline[1:]:
            seg_km = _haversine_km(prev[1], prev[0], cur[1], cur[0])
            if acc + seg_km >= target:
                t = (target - acc) / max(seg_km, 1e-9)
                lon = prev[0] + (cur[0] - prev[0]) * t
                lat = prev[1] + (cur[1] - prev[1]) * t
                return float(lat), float(lon)
            acc += seg_km
            prev = cur
        return float(polyline[-1][1]), float(polyline[-1][0])
    except Exception:
        return None, None


def _resolve_critical_coords(crit_seg, danger_markers, g_lat, g_lon, route_polyline=None):
    """
    FIX #2: Lấy tọa độ thực của điểm nguy hiểm cần tránh.
    Ưu tiên: lat/lon trong segment → tra cứu danger_markers gần nhất theo route_km
    → điểm đỏ gần nhất theo khoảng cách thực từ GPS.
    Trả về (lat, lon) hoặc (None, None) nếu không tìm được.
    """
    crit = crit_seg or {}
    # A) segment đã có tọa độ
    ilat = crit.get("lat") or crit.get("center_lat")
    ilon = crit.get("lon") or crit.get("center_lon")
    if ilat is not None and ilon is not None:
        return float(ilat), float(ilon)

    # A2) Nếu AI Forecast chỉ có route_km, quy đổi route_km → lat/lon trên polyline.
    route_km_target = crit.get("route_km")
    if route_km_target is not None and route_polyline:
        plat, plon = _coord_at_route_km(route_polyline, route_km_target)
        if plat is not None and plon is not None:
            return plat, plon

    # B) Tìm trong danger_markers điểm đỏ gần route_km của segment
    if route_km_target is not None and danger_markers:
        best, best_diff = None, 999.0
        for d in danger_markers:
            dlat = d.get("lat") or d.get("center_lat")
            dlon = d.get("lon") or d.get("center_lon")
            if dlat is None or dlon is None:
                continue
            rkm = d.get("route_km")
            if rkm is not None:
                diff = abs(float(rkm) - float(route_km_target))
                if diff < best_diff:
                    best_diff = diff
                    best = d
        if best and best_diff <= 15.0:
            return float(best.get("lat") or best.get("center_lat")), \
                   float(best.get("lon") or best.get("center_lon"))

    # C) Điểm đỏ (score >= 85%) gần GPS nhất phía trước
    if danger_markers and g_lat is not None and g_lon is not None:
        red_pts = [(d, _haversine_km(g_lat, g_lon,
                        float(d.get("lat") or d.get("center_lat") or g_lat),
                        float(d.get("lon") or d.get("center_lon") or g_lon)))
                   for d in danger_markers
                   if (d.get("lat") or d.get("center_lat")) and float(d.get("score", 0)) >= RED_RISK_THRESHOLD]
        if red_pts:
            red_pts.sort(key=lambda x: x[1])
            nearest = red_pts[0][0]
            return (float(nearest.get("lat") or nearest.get("center_lat")),
                    float(nearest.get("lon") or nearest.get("center_lon")))

    return None, None


def _route_passes_danger(polyline, danger_markers, threshold_score=RED_RISK_THRESHOLD, check_radius_km=1.5):
    """
    FIX #3: Kiểm tra tuyến mới có còn đi qua vùng nguy hiểm cao không.
    Trả về (passes: bool, max_score: float, count: int).
    """
    if not polyline or not danger_markers:
        return False, 0.0, 0
    red_markers = [d for d in danger_markers
                   if float(d.get("score", 0)) >= threshold_score
                   and (d.get("lat") or d.get("center_lat"))]
    if not red_markers:
        return False, 0.0, 0

    max_score = 0.0
    hit_count = 0
    # Lấy mẫu polyline (mỗi 5 điểm) để không quá chậm
    sample = polyline[::5] + ([polyline[-1]] if polyline else [])
    for d in red_markers:
        dlat = float(d.get("lat") or d.get("center_lat"))
        dlon = float(d.get("lon") or d.get("center_lon"))
        dscore = float(d.get("score", 0))
        for pt in sample:
            pt_lat, pt_lon = pt[1], pt[0]  # polyline là [lon, lat]
            if _haversine_km(pt_lat, pt_lon, dlat, dlon) <= check_radius_km:
                hit_count += 1
                max_score = max(max_score, dscore)
                break  # đủ 1 điểm trên tuyến là tính là "đi qua"
    return hit_count > 0, max_score, hit_count



def _polyline_cumulative_km(polyline):
    """Trả về list khoảng cách cộng dồn theo polyline [lon, lat]."""
    try:
        if not polyline:
            return []
        cum = [0.0]
        total = 0.0
        for a, b in zip(polyline[:-1], polyline[1:]):
            try:
                total += _haversine_km(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
            except Exception:
                total += 0.0
            cum.append(total)
        return cum
    except Exception:
        return []


def _nearest_route_km_to_coord(polyline, lat, lon):
    """Tìm km gần nhất trên tuyến với tọa độ lat/lon, dùng để xác định vị trí điểm đỏ trên route."""
    try:
        if not polyline:
            return 0.0
        cum = _polyline_cumulative_km(polyline)
        best_i, best_d = 0, 10**9
        for i, pt in enumerate(polyline):
            try:
                d = _haversine_km(float(lat), float(lon), float(pt[1]), float(pt[0]))
            except Exception:
                d = 10**9
            if d < best_d:
                best_d, best_i = d, i
        return float(cum[best_i] if best_i < len(cum) else 0.0)
    except Exception:
        return 0.0


def _polyline_prefix_until_km(polyline, km):
    """Lấy phần polyline từ đầu tuyến đến km chỉ định, có nội suy điểm cắt."""
    try:
        if not polyline:
            return []
        km = max(0.0, float(km or 0.0))
        total = _distance_km_from_polyline(polyline)
        if km <= 0:
            return [[float(polyline[0][0]), float(polyline[0][1])]]
        if km >= total:
            return [[float(x[0]), float(x[1])] for x in polyline]
        pts = [[float(polyline[0][0]), float(polyline[0][1])]]
        acc = 0.0
        prev = polyline[0]
        for cur in polyline[1:]:
            seg = _haversine_km(float(prev[1]), float(prev[0]), float(cur[1]), float(cur[0]))
            if acc + seg < km:
                pts.append([float(cur[0]), float(cur[1])])
                acc += seg
                prev = cur
                continue
            lat, lon = _coord_at_route_km(polyline, km)
            if lat is not None and lon is not None:
                pts.append([float(lon), float(lat)])
            break
        return pts
    except Exception:
        return [[float(x[0]), float(x[1])] for x in (polyline or [])]


def _polyline_suffix_from_km(polyline, km):
    """Lấy phần polyline từ km chỉ định đến cuối tuyến, có nội suy điểm cắt."""
    try:
        if not polyline:
            return []
        km = max(0.0, float(km or 0.0))
        total = _distance_km_from_polyline(polyline)
        if km <= 0:
            return [[float(x[0]), float(x[1])] for x in polyline]
        if km >= total:
            return [[float(polyline[-1][0]), float(polyline[-1][1])]]
        pts = []
        lat, lon = _coord_at_route_km(polyline, km)
        if lat is not None and lon is not None:
            pts.append([float(lon), float(lat)])
        cum = _polyline_cumulative_km(polyline)
        for i, pt in enumerate(polyline):
            if i < len(cum) and cum[i] > km:
                pts.append([float(pt[0]), float(pt[1])])
        if not pts or pts[-1] != [float(polyline[-1][0]), float(polyline[-1][1])]:
            pts.append([float(polyline[-1][0]), float(polyline[-1][1])])
        return pts
    except Exception:
        return [[float(x[0]), float(x[1])] for x in (polyline or [])]


def _polyline_segment_between_km(polyline, start_km, end_km):
    """Lấy đoạn polyline nằm giữa start_km và end_km để so sánh đoạn cục bộ."""
    try:
        start_km = max(0.0, float(start_km or 0.0))
        end_km = max(start_km, float(end_km or start_km))
        if not polyline:
            return []
        total = _distance_km_from_polyline(polyline)
        start_km = min(start_km, total)
        end_km = min(end_km, total)
        pts = []
        slat, slon = _coord_at_route_km(polyline, start_km)
        elat, elon = _coord_at_route_km(polyline, end_km)
        if slat is not None and slon is not None:
            pts.append([float(slon), float(slat)])
        cum = _polyline_cumulative_km(polyline)
        for i, pt in enumerate(polyline):
            if i < len(cum) and start_km < cum[i] < end_km:
                pts.append([float(pt[0]), float(pt[1])])
        if elat is not None and elon is not None:
            pts.append([float(elon), float(elat)])
        return _dedupe_polyline(pts)
    except Exception:
        return []


def _dedupe_polyline(polyline, precision=6):
    """Xóa các điểm liền kề trùng nhau để Folium/OSRM không lỗi."""
    out = []
    last = None
    for p in polyline or []:
        try:
            cur = [float(p[0]), float(p[1])]
            key = (round(cur[0], precision), round(cur[1], precision))
            if key != last:
                out.append(cur)
                last = key
        except Exception:
            continue
    return out


def _splice_local_reroute(old_polyline, local_polyline, entry_km, exit_km):
    """Ghép: đầu tuyến cũ + tuyến vòng cục bộ quanh điểm đỏ + cuối tuyến cũ."""
    prefix = _polyline_prefix_until_km(old_polyline, entry_km)
    suffix = _polyline_suffix_from_km(old_polyline, exit_km)
    return _dedupe_polyline((prefix or []) + (local_polyline or []) + (suffix or []))


def _accept_copilot_reroute(router, risk_engine, weather_api, mode_fallback, route_fallback=None):
    """
    Đổi tuyến theo Copilot khi có chấm đỏ >= 85%.

    Bản sửa theo yêu cầu mới:
    - CHỈ đổi tuyến cục bộ trong vùng 10 km quanh điểm đỏ.
    - Giữ nguyên các đoạn xa điểm đỏ.
    - Không đổi cả tuyến dài từ GPS hiện tại đến đích.
    - Nếu không tìm được đoạn vòng cục bộ hợp lệ thì báo rõ lý do, không âm thầm thay tuyến.
    """
    ss = st.session_state
    mode = ss.get("nav_mode") or ss.get("last_mode") or mode_fallback
    route_fallback = route_fallback or {}
    orig_polyline = route_fallback.get("polyline") or ss.get("nav_polyline") or ss.get("last_polyline") or []
    if not orig_polyline or len(orig_polyline) < 2:
        return False, "Chưa có tuyến hiện tại để đổi tuyến cục bộ quanh điểm đỏ."

    try:
        crit = ss.get("copilot_critical_segment") or {}
        if _copilot_segment_score(crit) < RED_RISK_THRESHOLD:
            return False, "Hiện chưa có chấm đỏ từ 85% trở lên nên chưa cần đổi tuyến."

        # GPS chỉ dùng để hỗ trợ xác định chấm đỏ nếu segment thiếu tọa độ; KHÔNG dùng để đổi cả tuyến.
        g_lat = ss.get("nav_gps_lat")
        g_lon = ss.get("nav_gps_lon")
        all_danger = ss.get("last_danger_markers") or []
        avoid_lat, avoid_lon = _resolve_critical_coords(
            crit, all_danger, g_lat, g_lon, route_polyline=orig_polyline
        )
        if avoid_lat is None or avoid_lon is None:
            return False, "Copilot chưa xác định được tọa độ chấm đỏ gần nhất để né, nên không đổi tuyến tự động."

        total_old_km = _distance_km_from_polyline(orig_polyline)
        red_route_km = crit.get("route_km")
        if red_route_km is None:
            red_route_km = _nearest_route_km_to_coord(orig_polyline, avoid_lat, avoid_lon)
        red_route_km = max(0.0, min(float(red_route_km or 0.0), total_old_km))

        # VÙNG XỬ LÝ CỤC BỘ: chỉ thay đoạn từ km đỏ - 10 đến km đỏ + 10.
        local_radius_km = 10.0
        entry_km = max(0.0, red_route_km - local_radius_km)
        exit_km = min(total_old_km, red_route_km + local_radius_km)
        if exit_km - entry_km < 1.0:
            return False, "Vùng đổi tuyến quanh điểm đỏ quá ngắn nên không thể tạo tuyến vòng cục bộ."

        entry_lat, entry_lon = _coord_at_route_km(orig_polyline, entry_km)
        exit_lat, exit_lon = _coord_at_route_km(orig_polyline, exit_km)
        if entry_lat is None or entry_lon is None or exit_lat is None or exit_lon is None:
            return False, "Không xác định được điểm vào/ra vùng 10 km quanh điểm đỏ."

        old_local_segment = _polyline_segment_between_km(orig_polyline, entry_km, exit_km)
        old_local_km = _distance_km_from_polyline(old_local_segment)
        avoid_label = crit.get("hazard_label") or crit.get("label") or "điểm đỏ gần nhất"
        avoid_marker = {
            "lat": float(avoid_lat),
            "lon": float(avoid_lon),
            "score": max(float(_copilot_segment_score(crit) or 0), RED_RISK_THRESHOLD),
            "label": avoid_label,
        }

        candidates = []
        rejected_reasons = []

        # Chỉ route từ điểm vào vùng 10 km đến điểm ra vùng 10 km.
        # Không dùng GPS→đích, nên các đoạn xa điểm đỏ vẫn giữ nguyên.
        if hasattr(router, "reroute_around_incident"):
            for avoid_radius in [1.5, 2.5, 3.5, 5.0, 7.5, 10.0]:
                try:
                    local_rt = router.reroute_around_incident(
                        (float(entry_lat), float(entry_lon)),
                        (float(exit_lat), float(exit_lon)),
                        float(avoid_lat), float(avoid_lon),
                        mode=mode,
                        avoid_radius_km=avoid_radius,
                    )
                    if local_rt and not local_rt.get("fallback") and local_rt.get("polyline"):
                        _apply_avg_speed_timing(local_rt, mode)
                        candidates.append((f"đoạn vòng cục bộ bán kính né {avoid_radius:g} km", local_rt))
                except Exception as e:
                    rejected_reasons.append(f"bán kính né {avoid_radius:g} km lỗi: {e}")

        # Fallback mềm: tính lại đoạn entry→exit bình thường, nhưng chỉ chấp nhận nếu đoạn này thật sự né điểm đỏ.
        try:
            local_direct = router.get_route((float(entry_lat), float(entry_lon)), (float(exit_lat), float(exit_lon)), mode=mode)
            if local_direct and local_direct.get("polyline"):
                _apply_avg_speed_timing(local_direct, mode)
                candidates.append(("đoạn cục bộ tính lại entry→exit", local_direct))
        except Exception:
            pass

        if not candidates:
            return False, "Không tính được đoạn tuyến vòng cục bộ trong bán kính 10 km quanh điểm đỏ."

        accepted = []
        for name, local_rt in candidates:
            local_poly = local_rt.get("polyline") or []
            if not local_poly or len(local_poly) < 2:
                rejected_reasons.append(f"{name}: không có polyline hợp lệ")
                continue

            local_km = _get_route_distance_km(local_rt) or _distance_km_from_polyline(local_poly)
            if old_local_km and local_km > max(old_local_km * 2.8, old_local_km + 25.0):
                rejected_reasons.append(f"{name}: đoạn vòng cục bộ quá dài ({local_km:.1f} km so với {old_local_km:.1f} km)")
                continue

            # Đoạn vòng không được còn đi sát điểm đỏ chính.
            still_hits_red, _, _ = _route_passes_danger(
                local_poly, [avoid_marker], threshold_score=RED_RISK_THRESHOLD, check_radius_km=1.8
            )
            if still_hits_red:
                rejected_reasons.append(f"{name}: đoạn vòng vẫn đi sát điểm đỏ")
                continue

            # Đoạn vòng phải khác đoạn cũ quanh điểm đỏ, không cần cả tuyến khác toàn bộ.
            if old_local_segment:
                new_set = set((round(p[0], 3), round(p[1], 3)) for p in local_poly)
                old_set = set((round(p[0], 3), round(p[1], 3)) for p in old_local_segment)
                local_overlap = len(new_set & old_set) / max(1, len(new_set))
                if local_overlap > 0.92:
                    rejected_reasons.append(f"{name}: đoạn cục bộ gần như trùng đoạn cũ")
                    continue
            else:
                local_overlap = 0.0

            combined_poly = _splice_local_reroute(orig_polyline, local_poly, entry_km, exit_km)
            if not combined_poly or len(combined_poly) < 2:
                rejected_reasons.append(f"{name}: ghép tuyến thất bại")
                continue

            combined_km = _distance_km_from_polyline(combined_poly)
            # Không đổi cả tuyến dài: tổng tuyến chỉ nên tăng vừa phải và không vòng quá xa phi lý.
            if total_old_km and not is_reasonable_detour(total_old_km, combined_km):
                rejected_reasons.append(
                    f"{name}: tổng tuyến vòng quá xa ({combined_km:.1f} km; giới hạn {detour_limit_text(total_old_km)})"
                )
                continue

            # Kiểm tra toàn tuyến sau khi ghép không còn đi sát điểm đỏ chính.
            passes_main_red, _, _ = _route_passes_danger(
                combined_poly, [avoid_marker], threshold_score=RED_RISK_THRESHOLD, check_radius_km=1.8
            )
            if passes_main_red:
                rejected_reasons.append(f"{name}: tuyến sau ghép vẫn đi sát điểm đỏ")
                continue

            new_route = dict(route_fallback or {})
            if not new_route:
                new_route = dict(ss.get("last_routes", [{}])[0] if ss.get("last_routes") else {})
            new_route["polyline"] = combined_poly
            new_route["distance_km"] = combined_km
            new_route["distance_text"] = f"{combined_km:.1f} km"
            new_route["local_reroute_applied"] = True
            new_route["local_reroute_radius_km"] = local_radius_km
            new_route["local_reroute_entry_km"] = entry_km
            new_route["local_reroute_exit_km"] = exit_km
            new_route["local_reroute_red_km"] = red_route_km
            new_route["local_reroute_label"] = avoid_label
            # Step chi tiết cũ có thể không còn đúng 100%, nhưng giữ lại để không mất chức năng cũ.
            # Ưu tiên step của đoạn vòng nếu router có trả về.
            if local_rt.get("steps"):
                new_route["local_reroute_steps"] = local_rt.get("steps", [])
            _apply_avg_speed_timing(new_route, mode)

            legal_ok, legal_issues = validate_route_for_mode(new_route, mode)
            if not legal_ok:
                rejected_reasons.append(f"{name}: không hợp lệ với phương tiện ({'; '.join(legal_issues)})")
                continue

            avg_risk, new_analysis = _route_avg_risk_for_copilot(new_route, risk_engine)
            # Không chặn điểm đỏ xa điểm đỏ chính, vì yêu cầu chỉ đổi quanh điểm đỏ hiện tại.
            # Chỉ chặn nếu điểm đỏ mới nằm trong vùng xử lý 10 km quanh điểm đỏ.
            near_new_red = 0
            for seg in (new_analysis.get("danger_segments") or []):
                try:
                    score = float(seg.get("score") or 0)
                    if score < RED_RISK_THRESHOLD:
                        continue
                    slat = seg.get("lat") or seg.get("center_lat")
                    slon = seg.get("lon") or seg.get("center_lon")
                    if slat is not None and slon is not None:
                        if _haversine_km(float(slat), float(slon), float(avoid_lat), float(avoid_lon)) <= local_radius_km:
                            near_new_red += 1
                except Exception:
                    pass
            if near_new_red:
                rejected_reasons.append(f"{name}: phát sinh điểm đỏ mới trong vùng 10 km")
                continue

            accepted.append((avg_risk, combined_km, local_km, local_overlap, name, new_route))

        if not accepted:
            detail = "; ".join(rejected_reasons[:5]) if rejected_reasons else "các đoạn vòng cục bộ đều không đạt điều kiện"
            return False, f"Chưa tìm được đoạn vòng cục bộ quanh điểm đỏ trong bán kính 10 km. Lý do: {detail}."

        accepted.sort(key=lambda x: (x[0], x[2], x[3]))
        avg_risk, combined_km, local_km, local_overlap, chosen_name, new_rt = accepted[0]
        rem_poly = new_rt.get("polyline", [])

        ss["nav_polyline"] = rem_poly
        ss["nav_steps"] = new_rt.get("steps", ss.get("nav_steps", []))
        ss["nav_progress_idx"] = 0
        ss["nav_max_progress"] = 0
        ss["nav_offroute"] = False
        ss["nav_reroute_pl"] = None
        ss["nav_distance_left_osrm"] = _get_route_distance_km(new_rt)
        ss["last_incident_reroute"] = new_rt
        ss["last_routes"] = [new_rt]
        ss["last_selected"] = 0
        ss["last_polyline"] = rem_poly
        ss["last_route_km"] = _get_route_distance_km(new_rt)
        _clear_route_view_cache()

        now_dt = datetime.now()
        total_sec = _get_route_duration_seconds(new_rt)
        ss["auto_eta_last_ts"] = __import__("time").time()
        ss["auto_eta_distance_km"] = _get_route_distance_km(new_rt)
        ss["auto_eta_duration_text"] = new_rt.get("duration_text") or _format_duration_from_seconds(total_sec)
        ss["auto_eta_arrival"] = (now_dt + timedelta(seconds=total_sec)).strftime("%H:%M") if total_sec else "?"
        ss["auto_eta_updated_at"] = now_dt.strftime("%H:%M:%S")
        ss["auto_eta_status"] = "✅ Đã đổi đoạn tuyến cục bộ quanh điểm đỏ và cập nhật ETA."
        ss["copilot_last_action"] = (
            f"✅ Đã đổi {chosen_name}. Chỉ thay đoạn km {entry_km:.1f}–{exit_km:.1f} "
            f"quanh {avoid_label}; các đoạn xa điểm đỏ được giữ nguyên."
        )

        try:
            import __main__ as _main_app
            _init_ml_model_fn = getattr(_main_app, "init_ml_model", None)
            ml_model = _init_ml_model_fn() if callable(_init_ml_model_fn) else None
            if ml_model is not None and getattr(ml_model, "is_ready", False):
                fc, _, _ = _compute_route_forecast(rem_poly, new_rt, now_dt, risk_engine, ml_model, weather_api)
                ss["auto_eta_forecast"] = fc
                ss["last_route_risk_forecast"] = fc
                ss["auto_eta_ai_ready"] = True
                ss["auto_eta_ai_status"] = "✅ đã cập nhật sau đổi tuyến cục bộ"
        except Exception as e:
            ss["auto_eta_ai_status"] = f"⚠️ lỗi AI forecast sau đổi tuyến cục bộ: {e}"

        _persist_current_route_snapshot()
        return True, (
            f"Đã đổi tuyến cục bộ quanh điểm đỏ: chỉ thay đoạn km {entry_km:.1f}–{exit_km:.1f} "
            f"(vùng 10 km quanh điểm đỏ), giữ nguyên các đoạn xa. "
            f"Đoạn vòng mới dài {local_km:.1f} km; tổng tuyến {combined_km:.1f} km."
        )
    except Exception as e:
        return False, f"Lỗi đổi tuyến cục bộ quanh điểm đỏ: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# PATCH: Copilot local reroute must visibly change the route when possible.
# Định nghĩa lại cùng tên để ghi đè bản phía trên mà không đụng các chức năng khác.
# ─────────────────────────────────────────────────────────────────────────────
def _min_distance_polyline_to_coord_km(polyline, lat, lon, sample_step=1):
    """Khoảng cách nhỏ nhất từ polyline [lon,lat] tới tọa độ lat/lon."""
    try:
        if not polyline:
            return 999999.0
        best = 999999.0
        step = max(1, int(sample_step or 1))
        pts = list(polyline[::step])
        if polyline[-1] not in pts:
            pts.append(polyline[-1])
        for p in pts:
            try:
                best = min(best, _haversine_km(float(p[1]), float(p[0]), float(lat), float(lon)))
            except Exception:
                pass
        return float(best)
    except Exception:
        return 999999.0


def _local_detour_waypoints_around_point(lat, lon, radius_km=5.0):
    """Sinh các waypoint vòng quanh điểm đỏ, chỉ dùng cho đoạn cục bộ entry→exit."""
    try:
        import math as _math
        lat = float(lat); lon = float(lon); radius_km = float(radius_km)
        out = []
        # 8 hướng chính. Các điểm này nằm quanh điểm đỏ, không làm đổi cả tuyến dài.
        for bearing in [0, 45, 90, 135, 180, 225, 270, 315]:
            br = _math.radians(bearing)
            dlat = (radius_km / 111.32) * _math.cos(br)
            dlon = (radius_km / max(20.0, 111.32 * _math.cos(_math.radians(lat)))) * _math.sin(br)
            out.append((lat + dlat, lon + dlon))
        return out
    except Exception:
        return []


def _apply_new_local_reroute_to_session(new_rt, mode, local_km, combined_km, entry_km, exit_km, avoid_label, chosen_name, risk_engine, weather_api):
    """Cập nhật toàn bộ session để rerun hiển thị tuyến mới ngay."""
    ss = st.session_state
    rem_poly = new_rt.get("polyline", [])

    ss["nav_polyline"] = rem_poly
    ss["nav_steps"] = new_rt.get("steps", ss.get("nav_steps", []))
    ss["nav_progress_idx"] = 0
    ss["nav_max_progress"] = 0
    ss["nav_offroute"] = False
    ss["nav_reroute_pl"] = None
    ss["nav_distance_left_osrm"] = _get_route_distance_km(new_rt)

    ss["last_incident_reroute"] = new_rt
    ss["last_routes"] = [new_rt]
    ss["last_selected"] = 0
    ss["last_polyline"] = rem_poly
    ss["last_route_km"] = _get_route_distance_km(new_rt)

    # Xóa mọi cache/phân tích tuyến cũ để map và Copilot buộc tính lại theo polyline mới.
    for _k in [
        "last_compared", "route_view_cache", "route_view_cache_key",
        "last_danger_markers", "last_danger_markers_raw", "last_rest_stops",
        "last_route_risk_forecast", "auto_eta_forecast",
        "copilot_critical_segment",
    ]:
        ss.pop(_k, None)
    _clear_route_view_cache()

    now_dt = datetime.now()
    total_sec = _get_route_duration_seconds(new_rt)
    ss["auto_eta_last_ts"] = __import__("time").time()
    ss["auto_eta_distance_km"] = _get_route_distance_km(new_rt)
    ss["auto_eta_duration_text"] = new_rt.get("duration_text") or _format_duration_from_seconds(total_sec)
    ss["auto_eta_arrival"] = (now_dt + timedelta(seconds=total_sec)).strftime("%H:%M") if total_sec else "?"
    ss["auto_eta_updated_at"] = now_dt.strftime("%H:%M:%S")
    ss["auto_eta_status"] = "✅ Đã đổi đoạn tuyến cục bộ quanh điểm đỏ và cập nhật ETA."
    ss["copilot_last_action"] = (
        f"✅ Đã đổi {chosen_name}. Chỉ thay đoạn km {entry_km:.1f}–{exit_km:.1f} "
        f"quanh {avoid_label}; các đoạn xa điểm đỏ được giữ nguyên."
    )
    ss["__copilot_reroute_nonce"] = int(ss.get("__copilot_reroute_nonce", 0)) + 1

    try:
        import __main__ as _main_app
        _init_ml_model_fn = getattr(_main_app, "init_ml_model", None)
        ml_model = _init_ml_model_fn() if callable(_init_ml_model_fn) else None
        if ml_model is not None and getattr(ml_model, "is_ready", False):
            fc, _, _ = _compute_route_forecast(rem_poly, new_rt, now_dt, risk_engine, ml_model, weather_api)
            ss["auto_eta_forecast"] = fc
            ss["last_route_risk_forecast"] = fc
            ss["auto_eta_ai_ready"] = True
            ss["auto_eta_ai_status"] = "✅ đã cập nhật sau đổi tuyến cục bộ"
    except Exception as e:
        ss["auto_eta_ai_status"] = f"⚠️ lỗi AI forecast sau đổi tuyến cục bộ: {e}"

    _persist_current_route_snapshot()
    return True, (
        f"Đã đổi tuyến cục bộ quanh điểm đỏ: chỉ thay đoạn km {entry_km:.1f}–{exit_km:.1f} "
        f"(vùng 10 km quanh điểm đỏ), giữ nguyên các đoạn xa. "
        f"Đoạn vòng mới dài {local_km:.1f} km; tổng tuyến {combined_km:.1f} km."
    )


def _accept_copilot_reroute(router, risk_engine, weather_api, mode_fallback, route_fallback=None):
    """
    Đổi tuyến theo Copilot khi có chấm đỏ >= 85%.

    Bản vá này giải quyết lỗi bấm "Đồng ý đổi tuyến" nhưng nhìn như không xảy ra gì:
    - Chỉ route lại đoạn cục bộ 10 km trước/sau điểm đỏ.
    - Tạo thêm nhiều waypoint vòng quanh điểm đỏ nếu hàm reroute_around_incident trả tuyến trùng.
    - Nếu bộ lọc quá nghiêm, vẫn chọn đoạn vòng tốt nhất có thay đổi thấy được.
    - Xóa cache cũ và rerun sẽ render tuyến mới ngay.
    """
    ss = st.session_state
    mode = ss.get("nav_mode") or ss.get("last_mode") or mode_fallback
    route_fallback = route_fallback or {}
    orig_polyline = route_fallback.get("polyline") or ss.get("nav_polyline") or ss.get("last_polyline") or []
    if not orig_polyline or len(orig_polyline) < 2:
        return False, "Chưa có tuyến hiện tại để đổi tuyến cục bộ quanh điểm đỏ."

    try:
        crit = ss.get("copilot_critical_segment") or {}
        if _copilot_segment_score(crit) < RED_RISK_THRESHOLD:
            return False, "Hiện chưa có chấm đỏ từ 85% trở lên nên chưa cần đổi tuyến."

        g_lat = ss.get("nav_gps_lat")
        g_lon = ss.get("nav_gps_lon")
        all_danger = ss.get("last_danger_markers") or []
        avoid_lat, avoid_lon = _resolve_critical_coords(
            crit, all_danger, g_lat, g_lon, route_polyline=orig_polyline
        )
        if avoid_lat is None or avoid_lon is None:
            return False, "Copilot chưa xác định được tọa độ chấm đỏ gần nhất để né."

        total_old_km = _distance_km_from_polyline(orig_polyline)
        red_route_km = crit.get("route_km")
        if red_route_km is None:
            red_route_km = _nearest_route_km_to_coord(orig_polyline, avoid_lat, avoid_lon)
        red_route_km = max(0.0, min(float(red_route_km or 0.0), total_old_km))

        local_radius_km = 10.0
        entry_km = max(0.0, red_route_km - local_radius_km)
        exit_km = min(total_old_km, red_route_km + local_radius_km)
        if exit_km - entry_km < 1.0:
            return False, "Vùng đổi tuyến quanh điểm đỏ quá ngắn nên không thể tạo tuyến vòng cục bộ."

        entry_lat, entry_lon = _coord_at_route_km(orig_polyline, entry_km)
        exit_lat, exit_lon = _coord_at_route_km(orig_polyline, exit_km)
        if entry_lat is None or entry_lon is None or exit_lat is None or exit_lon is None:
            return False, "Không xác định được điểm vào/ra vùng 10 km quanh điểm đỏ."

        old_local_segment = _polyline_segment_between_km(orig_polyline, entry_km, exit_km)
        old_local_km = _distance_km_from_polyline(old_local_segment)
        old_min_red_dist = _min_distance_polyline_to_coord_km(old_local_segment, avoid_lat, avoid_lon)
        avoid_label = crit.get("hazard_label") or crit.get("label") or "điểm đỏ gần nhất"
        avoid_marker = {
            "lat": float(avoid_lat),
            "lon": float(avoid_lon),
            "score": max(float(_copilot_segment_score(crit) or 0), RED_RISK_THRESHOLD),
            "label": avoid_label,
        }

        candidates = []
        rejected_reasons = []

        def _add_candidate(name, rt):
            if rt and rt.get("polyline"):
                _apply_avg_speed_timing(rt, mode)
                candidates.append((name, rt))

        # 1) Dùng hàm reroute_around_incident nếu router có.
        if hasattr(router, "reroute_around_incident"):
            for avoid_radius in [1.0, 1.5, 2.5, 3.5, 5.0, 7.5, 10.0]:
                try:
                    local_rt = router.reroute_around_incident(
                        (float(entry_lat), float(entry_lon)),
                        (float(exit_lat), float(exit_lon)),
                        float(avoid_lat), float(avoid_lon),
                        mode=mode,
                        avoid_radius_km=avoid_radius,
                    )
                    _add_candidate(f"đoạn vòng cục bộ bán kính né {avoid_radius:g} km", local_rt)
                except Exception as e:
                    rejected_reasons.append(f"reroute_around_incident {avoid_radius:g} km lỗi: {e}")

        # 2) Tự thử waypoint vòng quanh điểm đỏ, vẫn chỉ route entry→exit.
        # Đây là phần giúp nút đổi tuyến không bị im lặng khi reroute_around_incident trả tuyến trùng.
        for radius in [2.0, 4.0, 6.0, 8.0, 10.0]:
            for wp in _local_detour_waypoints_around_point(avoid_lat, avoid_lon, radius):
                try:
                    rt = router.get_route(
                        (float(entry_lat), float(entry_lon)),
                        (float(exit_lat), float(exit_lon)),
                        mode=mode,
                        waypoints=[wp],
                    )
                    _add_candidate(f"đoạn vòng cục bộ qua waypoint {radius:g} km", rt)
                except Exception:
                    pass

        # 3) Direct local fallback để có dữ liệu so sánh, nhưng chỉ chọn nếu thật sự khác.
        try:
            direct = router.get_route((float(entry_lat), float(entry_lon)), (float(exit_lat), float(exit_lon)), mode=mode)
            _add_candidate("đoạn cục bộ entry→exit", direct)
        except Exception:
            pass

        if not candidates:
            return False, "Không tính được đoạn tuyến vòng cục bộ trong bán kính 10 km quanh điểm đỏ."

        accepted = []
        fallback_pool = []
        old_set = set((round(p[0], 3), round(p[1], 3)) for p in (old_local_segment or []))

        for name, local_rt in candidates:
            local_poly = local_rt.get("polyline") or []
            if not local_poly or len(local_poly) < 2:
                rejected_reasons.append(f"{name}: không có polyline hợp lệ")
                continue

            local_km = _get_route_distance_km(local_rt) or _distance_km_from_polyline(local_poly)
            if old_local_km and local_km > max(old_local_km * 3.5, old_local_km + 35.0):
                rejected_reasons.append(f"{name}: đoạn vòng quá dài ({local_km:.1f} km so với {old_local_km:.1f} km)")
                continue

            new_set = set((round(p[0], 3), round(p[1], 3)) for p in local_poly)
            local_overlap = len(new_set & old_set) / max(1, len(new_set)) if old_set else 0.0
            min_red_dist = _min_distance_polyline_to_coord_km(local_poly, avoid_lat, avoid_lon)

            combined_poly = _splice_local_reroute(orig_polyline, local_poly, entry_km, exit_km)
            if not combined_poly or len(combined_poly) < 2:
                rejected_reasons.append(f"{name}: ghép tuyến thất bại")
                continue

            combined_km = _distance_km_from_polyline(combined_poly)
            if total_old_km and not is_reasonable_detour(total_old_km, combined_km):
                rejected_reasons.append(
                    f"{name}: tổng tuyến vòng quá xa ({combined_km:.1f} km; giới hạn {detour_limit_text(total_old_km)})"
                )
                continue

            new_route = dict(route_fallback or {})
            if not new_route:
                new_route = dict(ss.get("last_routes", [{}])[0] if ss.get("last_routes") else {})
            new_route["polyline"] = combined_poly
            new_route["distance_km"] = combined_km
            new_route["distance_text"] = f"{combined_km:.1f} km"
            new_route["local_reroute_applied"] = True
            new_route["local_reroute_radius_km"] = local_radius_km
            new_route["local_reroute_entry_km"] = entry_km
            new_route["local_reroute_exit_km"] = exit_km
            new_route["local_reroute_red_km"] = red_route_km
            new_route["local_reroute_label"] = avoid_label
            new_route["local_reroute_min_red_dist_km"] = round(min_red_dist, 3)
            if local_rt.get("steps"):
                new_route["local_reroute_steps"] = local_rt.get("steps", [])
            _apply_avg_speed_timing(new_route, mode)

            legal_ok, legal_issues = validate_route_for_mode(new_route, mode)
            if not legal_ok:
                rejected_reasons.append(f"{name}: không hợp lệ với phương tiện ({'; '.join(legal_issues)})")
                continue

            avg_risk, new_analysis = _route_avg_risk_for_copilot(new_route, risk_engine)

            # Strict: tuyến phải khác rõ và xa điểm đỏ hơn.
            strict_ok = (local_overlap <= 0.92 and min_red_dist >= max(0.8, old_min_red_dist + 0.2))

            # Best-effort: có thay đổi nhìn thấy trên map, dù không né hoàn hảo.
            visible_change = (local_overlap <= 0.97 and min_red_dist >= max(0.25, old_min_red_dist))

            item = (avg_risk, -min_red_dist, local_km, local_overlap, name, new_route)
            if strict_ok:
                accepted.append(item)
            elif visible_change:
                fallback_pool.append(item)
            else:
                rejected_reasons.append(
                    f"{name}: gần như trùng tuyến cũ hoặc không xa điểm đỏ hơn "
                    f"(overlap {local_overlap:.0%}, cách đỏ {min_red_dist:.2f} km)"
                )

        chosen_from = "strict"
        if accepted:
            pool = accepted
        elif fallback_pool:
            pool = fallback_pool
            chosen_from = "best_effort"
        else:
            detail = "; ".join(rejected_reasons[:6]) if rejected_reasons else "các đoạn vòng đều trùng tuyến cũ"
            return False, f"Chưa tìm được đoạn vòng cục bộ quanh điểm đỏ. Lý do: {detail}."

        # Ưu tiên xa điểm đỏ hơn, rồi rủi ro thấp, rồi ngắn hơn.
        pool.sort(key=lambda x: (x[1], x[0], x[2], x[3]))
        avg_risk, neg_min_red_dist, local_km, local_overlap, chosen_name, new_rt = pool[0]
        combined_km = _get_route_distance_km(new_rt)

        ok, msg = _apply_new_local_reroute_to_session(
            new_rt, mode, local_km, combined_km, entry_km, exit_km,
            avoid_label, chosen_name, risk_engine, weather_api,
        )
        if ok and chosen_from == "best_effort":
            msg += " Lưu ý: đây là phương án tốt nhất tìm được trong vùng 10 km; đường thực tế có thể vẫn gần điểm đỏ nếu khu vực ít đường thay thế."
        return ok, msg
    except Exception as e:
        return False, f"Lỗi đổi tuyến cục bộ quanh điểm đỏ: {e}"

def _accept_copilot_rest(router, risk_engine, weather_api, mode_fallback, delay_min=15):
    """Sau khi người dùng đã xác nhận nghỉ: dời giờ xuất phát lại, chạy lại AI forecast theo ETA mới."""
    ss = st.session_state
    mode = ss.get("nav_mode") or ss.get("last_mode") or mode_fallback
    poly = ss.get("nav_polyline") or ss.get("last_polyline") or []
    if not poly:
        return False, "Chưa có tuyến để cập nhật sau khi nghỉ."
    dist = ss.get("nav_distance_left_osrm") or ss.get("auto_eta_distance_km") or ss.get("last_route_km") or _distance_km_from_polyline(poly)
    route_tmp = {"polyline": poly, "distance_km": float(dist or 0), "steps": ss.get("nav_steps", [])}
    _apply_avg_speed_timing(route_tmp, mode)
    start_dt = datetime.now() + timedelta(minutes=int(delay_min))
    total_sec = _get_route_duration_seconds(route_tmp)
    ss["auto_eta_last_ts"] = __import__("time").time()
    ss["auto_eta_distance_km"] = _get_route_distance_km(route_tmp)
    ss["auto_eta_duration_text"] = route_tmp.get("duration_text")
    ss["auto_eta_arrival"] = (start_dt + timedelta(seconds=total_sec)).strftime("%H:%M") if total_sec else "?"
    ss["auto_eta_updated_at"] = datetime.now().strftime("%H:%M:%S")
    ss["auto_eta_status"] = f"✅ Đã xác nhận nghỉ {delay_min} phút và cập nhật ETA."
    ss["copilot_last_action"] = f"⏸️ Đã xác nhận nghỉ {delay_min} phút rồi cập nhật lại dự báo."
    try:
        import __main__ as _main_app
        _init_ml_model_fn = getattr(_main_app, "init_ml_model", None)
        ml_model = _init_ml_model_fn() if callable(_init_ml_model_fn) else None
        if ml_model is not None and getattr(ml_model, "is_ready", False):
            fc, _, _ = _compute_route_forecast(poly, route_tmp, start_dt, risk_engine, ml_model, weather_api)
            ss["auto_eta_forecast"] = fc
            ss["auto_eta_ai_ready"] = True
            ss["auto_eta_ai_status"] = f"✅ đã cập nhật sau nghỉ {delay_min} phút"
    except Exception as e:
        ss["auto_eta_ai_status"] = f"⚠️ lỗi AI forecast sau nghỉ: {e}"
    return True, f"Đã cập nhật dự báo sau khi nghỉ {delay_min} phút."
