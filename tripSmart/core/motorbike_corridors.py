# core/motorbike_corridors.py
# Hành lang tuyến an toàn cho xe máy.
# Mục tiêu: tránh waypoint ngẫu nhiên làm tuyến vòng méo; ưu tiên các điểm trung gian thật.

from __future__ import annotations
from typing import Dict, List, Tuple

Coord = Tuple[float, float]  # (lat, lon)

# Các corridor được thiết kế để OSRM bị ép đi qua vùng/đường có thật,
# sau đó legal_route_filter.py vẫn kiểm tra lại cao tốc/ĐCT.
MOTORBIKE_CORRIDORS: List[Dict] = [
    {
        "name": "Lâm Đồng: Đạ Huoai/Bảo Lộc/Di Linh ↔ Đà Lạt tránh Liên Khương - Prenn",
        "region": "lam_dong_west_to_dalat",
        "bounds": {
            "lat_min": 11.20,
            "lat_max": 12.15,
            "lon_min": 107.35,
            "lon_max": 108.75,
        },
        # waypoint theo hướng tây/southwest -> Đà Lạt
        # Lưu ý: đây là tọa độ vùng/thị trấn, không phải điểm ngẫu nhiên giữa núi.
        "west_to_dalat": [
            ("Bảo Lộc", 11.5480, 107.8070),
            ("Di Linh", 11.5810, 108.0720),
            ("Đức Trọng", 11.7350, 108.3730),
            ("Đơn Dương", 11.7760, 108.4980),
            ("Trại Mát", 11.9290, 108.5050),
        ],
        "dalat_to_west": [
            ("Trại Mát", 11.9290, 108.5050),
            ("Đơn Dương", 11.7760, 108.4980),
            ("Đức Trọng", 11.7350, 108.3730),
            ("Di Linh", 11.5810, 108.0720),
            ("Bảo Lộc", 11.5480, 107.8070),
        ],
    },
    {
        "name": "Đồng Nai - TP.HCM: Long Thành ↔ TP.HCM tránh cao tốc",
        "region": "dong_nai_hcm",
        "bounds": {
            "lat_min": 10.45,
            "lat_max": 11.05,
            "lon_min": 106.45,
            "lon_max": 107.25,
        },
        "east_to_hcm": [
            ("Long Thành", 10.7890, 106.9570),
            ("Nhơn Trạch", 10.7110, 106.8860),
            ("Phà Cát Lái", 10.7700, 106.7890),
        ],
        "hcm_to_east": [
            ("Phà Cát Lái", 10.7700, 106.7890),
            ("Nhơn Trạch", 10.7110, 106.8860),
            ("Long Thành", 10.7890, 106.9570),
        ],
    },
    {
        "name": "Bắc - Nam ven biển: QL1A/QL20/QL27/QL14B tránh cao tốc",
        "region": "vietnam_north_south_coastal",
        "bounds": {
            "lat_min": 8.0,
            "lat_max": 23.5,
            "lon_min": 104.0,
            "lon_max": 109.6,
        },
        # Dùng các đô thị lớn nằm trên/gan QL1A và đường kết nối từ Đà Lạt ra ven biển.
        # Không phải graph pháp lý tuyệt đối; legal_route_filter.py vẫn kiểm tra lại cao tốc.
        "south_to_north": [
            ("Đà Lạt", 11.9404, 108.4583),
            ("Đức Trọng", 11.7350, 108.3730),
            ("Phan Rang", 11.5639, 108.9880),
            ("Nha Trang", 12.2388, 109.1967),
            ("Tuy Hòa", 13.0955, 109.3209),
            ("Quy Nhơn", 13.7765, 109.2237),
            ("Quảng Ngãi", 15.1205, 108.7923),
            ("Đà Nẵng", 16.0544, 108.2022),
            ("Huế", 16.4637, 107.5909),
            ("Đồng Hới", 17.4689, 106.6223),
            ("Vinh", 18.6796, 105.6813),
            ("Thanh Hóa", 19.8067, 105.7852),
            ("Ninh Bình", 20.2506, 105.9745),
            ("Hà Nội", 21.0245, 105.8412),
        ],
        "north_to_south": [
            ("Hà Nội", 21.0245, 105.8412),
            ("Ninh Bình", 20.2506, 105.9745),
            ("Thanh Hóa", 19.8067, 105.7852),
            ("Vinh", 18.6796, 105.6813),
            ("Đồng Hới", 17.4689, 106.6223),
            ("Huế", 16.4637, 107.5909),
            ("Đà Nẵng", 16.0544, 108.2022),
            ("Quảng Ngãi", 15.1205, 108.7923),
            ("Quy Nhơn", 13.7765, 109.2237),
            ("Tuy Hòa", 13.0955, 109.3209),
            ("Nha Trang", 12.2388, 109.1967),
            ("Phan Rang", 11.5639, 108.9880),
            ("Đức Trọng", 11.7350, 108.3730),
            ("Đà Lạt", 11.9404, 108.4583),
        ],
    },
    {
        "name": "Bắc - Nam phía Tây: đường Hồ Chí Minh/QL14 tránh cao tốc",
        "region": "vietnam_north_south_hcm_road",
        "bounds": {
            "lat_min": 8.0,
            "lat_max": 23.5,
            "lon_min": 103.0,
            "lon_max": 108.8,
        },
        "south_to_north": [
            ("Đà Lạt", 11.9404, 108.4583),
            ("Buôn Ma Thuột", 12.6667, 108.0500),
            ("Pleiku", 13.9833, 108.0000),
            ("Kon Tum", 14.3500, 108.0000),
            ("Thạnh Mỹ", 15.7530, 107.8390),
            ("A Lưới", 16.2300, 107.3000),
            ("Khe Sanh", 16.6270, 106.7290),
            ("Phong Nha", 17.6100, 106.3100),
            ("Tân Kỳ", 19.0470, 105.2670),
            ("Cẩm Thủy", 20.1830, 105.4700),
            ("Hòa Bình", 20.8170, 105.3380),
            ("Hà Nội", 21.0245, 105.8412),
        ],
        "north_to_south": [
            ("Hà Nội", 21.0245, 105.8412),
            ("Hòa Bình", 20.8170, 105.3380),
            ("Cẩm Thủy", 20.1830, 105.4700),
            ("Tân Kỳ", 19.0470, 105.2670),
            ("Phong Nha", 17.6100, 106.3100),
            ("Khe Sanh", 16.6270, 106.7290),
            ("A Lưới", 16.2300, 107.3000),
            ("Thạnh Mỹ", 15.7530, 107.8390),
            ("Kon Tum", 14.3500, 108.0000),
            ("Pleiku", 13.9833, 108.0000),
            ("Buôn Ma Thuột", 12.6667, 108.0500),
            ("Đà Lạt", 11.9404, 108.4583),
        ],
    },
]


def _in_bounds(pt: Coord, bounds: Dict[str, float]) -> bool:
    lat, lon = float(pt[0]), float(pt[1])
    return (
        bounds["lat_min"] <= lat <= bounds["lat_max"] and
        bounds["lon_min"] <= lon <= bounds["lon_max"]
    )


def _near_dalat(pt: Coord) -> bool:
    lat, lon = float(pt[0]), float(pt[1])
    return 11.82 <= lat <= 12.08 and 108.30 <= lon <= 108.65


def _west_lam_dong(pt: Coord) -> bool:
    lat, lon = float(pt[0]), float(pt[1])
    return 11.25 <= lat <= 11.75 and 107.35 <= lon <= 108.25


def _near_hcm(pt: Coord) -> bool:
    lat, lon = float(pt[0]), float(pt[1])
    return 10.60 <= lat <= 10.95 and 106.55 <= lon <= 106.90


def _near_long_thanh_dong_nai(pt: Coord) -> bool:
    lat, lon = float(pt[0]), float(pt[1])
    return 10.60 <= lat <= 10.95 and 106.85 <= lon <= 107.20


def _is_long_north_south_trip(o: Coord, d: Coord) -> bool:
    """Nhận diện tuyến Bắc - Nam dài, ví dụ Đà Lạt ↔ Hà Nội.

    Không cần đúng tên địa điểm; chỉ dựa vào tọa độ đã geocode.
    """
    lat1, lon1 = float(o[0]), float(o[1])
    lat2, lon2 = float(d[0]), float(d[1])
    return abs(lat1 - lat2) >= 4.5 and max(lat1, lat2) >= 16.0 and min(lat1, lat2) <= 14.5


def _filter_waypoints_between(origin: Coord, destination: Coord, named_points: List[Tuple[str, float, float]]) -> Tuple[List[Coord], List[str]]:
    """Giữ waypoint nằm giữa origin/destination theo chiều vĩ độ, bỏ điểm quá sát đầu/cuối."""
    o_lat, o_lon = float(origin[0]), float(origin[1])
    d_lat, d_lon = float(destination[0]), float(destination[1])
    going_north = d_lat > o_lat
    lat_lo = min(o_lat, d_lat) - 0.35
    lat_hi = max(o_lat, d_lat) + 0.35
    pts = []
    for name, lat, lon in named_points:
        lat = float(lat); lon = float(lon)
        if not (lat_lo <= lat <= lat_hi):
            continue
        # bỏ điểm gần origin/destination để OSRM không loop ở đầu/cuối
        if abs(lat - o_lat) < 0.25 and abs(lon - o_lon) < 0.35:
            continue
        if abs(lat - d_lat) < 0.25 and abs(lon - d_lon) < 0.35:
            continue
        pts.append((name, lat, lon))
    pts.sort(key=lambda x: x[1], reverse=not going_north)
    return [(lat, lon) for name, lat, lon in pts], [name for name, lat, lon in pts]


def get_motorbike_corridor_candidates(origin: Coord, destination: Coord) -> List[Dict]:
    """Trả về danh sách corridor phù hợp với cặp điểm origin/destination.

    Không dựa vào text người dùng; chỉ dựa vào tọa độ đã geocode.
    Mỗi candidate có: name, waypoints [(lat, lon), ...]
    """
    out: List[Dict] = []
    o = (float(origin[0]), float(origin[1]))
    d = (float(destination[0]), float(destination[1]))

    # Lâm Đồng: nếu một đầu gần Đà Lạt/Đức Trọng và đầu kia ở Đạ Huoai/Bảo Lộc/Di Linh
    if (_west_lam_dong(o) and _near_dalat(d)) or (_near_dalat(o) and _west_lam_dong(d)):
        c = MOTORBIKE_CORRIDORS[0]
        if _west_lam_dong(o) and _near_dalat(d):
            wps = c["west_to_dalat"]
        else:
            wps = c["dalat_to_west"]
        out.append({
            "name": c["name"],
            "region": c["region"],
            "waypoints": [(float(lat), float(lon)) for _name, lat, lon in wps],
            "labels": [str(_name) for _name, _lat, _lon in wps],
        })

    # TP.HCM - Long Thành/Đồng Nai
    if (_near_hcm(o) and _near_long_thanh_dong_nai(d)) or (_near_long_thanh_dong_nai(o) and _near_hcm(d)):
        c = MOTORBIKE_CORRIDORS[1]
        if _near_hcm(o) and _near_long_thanh_dong_nai(d):
            wps = c["hcm_to_east"]
        else:
            wps = c["east_to_hcm"]
        out.append({
            "name": c["name"],
            "region": c["region"],
            "waypoints": [(float(lat), float(lon)) for _name, lat, lon in wps],
            "labels": [str(_name) for _name, _lat, _lon in wps],
        })


    # Tuyến dài Bắc - Nam: thêm 2 hành lang thật để xe máy không phụ thuộc vào waypoint ngẫu nhiên.
    if _is_long_north_south_trip(o, d):
        for c in MOTORBIKE_CORRIDORS[2:4]:
            if d[0] >= o[0]:
                named_wps = c["south_to_north"]
            else:
                named_wps = c["north_to_south"]
            wps, labels = _filter_waypoints_between(o, d, named_wps)
            if wps:
                out.append({
                    "name": c["name"],
                    "region": c["region"],
                    "waypoints": wps,
                    "labels": labels,
                })

    return out
