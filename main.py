import os
import json
import math
import time
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hotel_monitor")

JST = timezone(timedelta(hours=9))

CONDITIONS_FILE = "conditions.json"
HISTORY_FILE = "history.json"
RESULT_CSV_FILE = "result.csv"
DOCS_DATA_FILE = os.path.join("docs", "data.json")
GEO_CACHE_FILE = ".geo_cache.json"
GEO_CACHE_VERSION = 2

RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "")
JALAN_API_KEY = os.environ.get("JALAN_API_KEY", "")


# =====================================================================
# 1. ジオコーディング (国土地理院API / Nominatim)
# =====================================================================
def load_geo_cache() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(GEO_CACHE_FILE):
        try:
            with open(GEO_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_geo_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    try:
        with open(GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"ジオキャッシュ保存失敗: {e}")


def geocode_destination(destination: str, geo_cache: Dict[str, Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    clean_dest = destination.strip()
    if not clean_dest:
        return None

    if clean_dest in geo_cache:
        c = geo_cache[clean_dest]
        if isinstance(c, dict) and c.get("v") == GEO_CACHE_VERSION and "lat" in c and "lon" in c:
            return float(c["lat"]), float(c["lon"])

    # 1. 国土地理院 ジオコーダー
    try:
        url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={urllib.parse.quote(clean_dest)}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0 and "geometry" in data[0]:
                coords = data[0]["geometry"].get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = float(coords[0]), float(coords[1])
                    geo_cache[clean_dest] = {"v": GEO_CACHE_VERSION, "lat": lat, "lon": lon}
                    return lat, lon
    except Exception as e:
        logger.debug(f"国土地理院ジオコーディング失敗 ({clean_dest}): {e}")

    # 2. OpenStreetMap Nominatim
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(clean_dest)}&format=json&countrycodes=jp"
        headers = {"User-Agent": "HotelMonitorApp/2.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                geo_cache[clean_dest] = {"v": GEO_CACHE_VERSION, "lat": lat, "lon": lon}
                return lat, lon
    except Exception as e:
        logger.debug(f"Nominatimジオコーディング失敗 ({clean_dest}): {e}")

    return None


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def calculate_walk_minutes(distance_m: float) -> int:
    return math.ceil(distance_m / 80.0)


# =====================================================================
# 2. 監視条件の読み込み（まとめ打ち展開・過去日程除外対応）
# =====================================================================
def load_conditions(file_path: str = CONDITIONS_FILE, geo_cache: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if geo_cache is None:
        geo_cache = {}

    if not os.path.exists(file_path):
        logger.warning(f"{file_path} が見つかりません。")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        try:
            raw_data = json.load(f)
        except Exception as e:
            logger.error(f"{file_path} の読み込みに失敗しました: {e}")
            return []

    today_date = datetime.now(JST).date()
    expanded_conditions = []

    for item in raw_data:
        if not isinstance(item, dict):
            continue

        status = item.get("status", "監視中")
        if status != "監視中":
            continue

        raw_dates = str(item.get("checkin_date", "")).strip()
        if not raw_dates:
            continue

        # カンマ区切りによる「まとめ打ち」を展開
        date_tokens = [d.strip() for d in raw_dates.replace("、", ",").split(",") if d.strip()]

        for d_str in date_tokens:
            cin_normalized = d_str.replace("/", "-")
            try:
                cin_date = datetime.strptime(cin_normalized, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"日付形式不正 ({d_str})。スキップします。")
                continue

            if cin_date < today_date:
                logger.warning(f"チェックイン日({cin_date})が過去のためスキップします。")
                continue

            dest = item.get("destination", "").strip()
            target_lat = item.get("target_lat")
            target_lon = item.get("target_lon")

            if (not target_lat or not target_lon) and dest:
                coords = geocode_destination(dest, geo_cache)
                if coords:
                    target_lat, target_lon = coords

            cond = {
                "id": item.get("id", f"trip-{cin_date.strftime('%Y%m%d')}-{dest}"),
                "management_name": item.get("management_name", f"{dest}出張" if dest else "出張"),
                "checkin_date": cin_date.strftime("%Y-%m-%d"),
                "stay_nights": int(item.get("stay_nights", 1)),
                "adult_num": int(item.get("adult_num", 1)),
                "destination": dest,
                "target_lat": float(target_lat) if target_lat else None,
                "target_lon": float(target_lon) if target_lon else None,
                "max_walk_minutes": int(item.get("max_walk_minutes", 15)),
                "budget_max": int(item.get("budget_max", 15000)),
                "booked_price": int(item["booked_price"]) if item.get("booked_price") else None,
                "smoking": item.get("smoking", "禁煙"),
                "breakfast": item.get("breakfast", "指定なし"),
                "min_review_rate": float(item.get("min_review_rate", 3.5)),
                "status": status,
            }
            expanded_conditions.append(cond)

    logger.info(f"有効な監視対象: {len(expanded_conditions)} 件")
    return expanded_conditions


# =====================================================================
# 3. 楽天トラベルAPI & じゃらんAPI & デモフォールバック
# =====================================================================
def fetch_rakuten_hotels(cond: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not RAKUTEN_APP_ID:
        return []

    lat = cond.get("target_lat")
    lon = cond.get("target_lon")
    if not lat or not lon:
        return []

    cin_str = cond["checkin_date"]
    cin_dt = datetime.strptime(cin_str, "%Y-%m-%d")
    cout_dt = cin_dt + timedelta(days=cond["stay_nights"])
    cout_str = cout_dt.strftime("%Y-%m-%d")

    radius_km = max(0.5, min(3.0, (cond["max_walk_minutes"] * 80) / 1000.0 * 1.3))

    params = {
        "applicationId": RAKUTEN_APP_ID,
        "format": "json",
        "checkinDate": cin_str,
        "checkoutDate": cout_str,
        "latitude": lat,
        "longitude": lon,
        "searchRadius": round(radius_km, 1),
        "adultNum": cond["adult_num"],
        "maxCharge": cond["budget_max"],
        "datumType": 1,
    }

    squeeze = []
    if cond["smoking"] == "禁煙":
        squeeze.append("kinen")
    if cond["breakfast"] == "朝食あり":
        squeeze.append("breakfast")
    if squeeze:
        params["squeezeConditions"] = ",".join(squeeze)

    url = "https://app.rakuten.co.jp/services/api/Travel/VacantHotelSearch/20170426"
    hotels = []
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for h_wrapper in data.get("hotels", []):
                h_info = h_wrapper.get("hotel", [])
                if not h_info or len(h_info) < 1:
                    continue
                basic = h_info[0].get("hotelBasicInfo", {})
                h_lat = float(basic.get("latitude", 0))
                h_lon = float(basic.get("longitude", 0))
                dist_m = haversine_distance_meters(lat, lon, h_lat, h_lon)
                walk_min = calculate_walk_minutes(dist_m)

                min_charge = basic.get("hotelMinCharge", 0)
                review_rate = float(basic.get("reviewAverage", 0.0) or 0.0)

                hotels.append({
                    "hotel_id": f"rakuten_{basic.get('hotelNo')}",
                    "hotel_name": basic.get("hotelName", ""),
                    "price": min_charge,
                    "walk_minutes": walk_min,
                    "review_rate": review_rate,
                    "agent": "楽天トラベル",
                    "plan_name": basic.get("hotelSpecial", "おすすめ宿泊プラン"),
                    "image_url": basic.get("hotelThumbnailUrl", "") or basic.get("hotelImageUrl", ""),
                    "url": basic.get("hotelInformationUrl", "https://travel.rakuten.co.jp/"),
                    "tags": [t for t in [cond["smoking"], cond["breakfast"] if cond["breakfast"] != "指定なし" else ""] if t],
                })
    except Exception as e:
        logger.warning(f"楽天API取得エラー: {e}")

    return hotels


def fetch_jalan_hotels(cond: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not JALAN_API_KEY:
        return []

    lat = cond.get("target_lat")
    lon = cond.get("target_lon")
    if not lat or not lon:
        return []

    cin_str = cond["checkin_date"].replace("-", "")
    radius_km = max(1.0, min(5.0, (cond["max_walk_minutes"] * 80) / 1000.0 * 1.5))

    params = {
        "key": JALAN_API_KEY,
        "stay_date": cin_str,
        "stay_count": cond["stay_nights"],
        "adult_num": cond["adult_num"],
        "max_charge": cond["budget_max"],
        "lat": lat,
        "lng": lon,
        "l_rad": round(radius_km, 1),
        "datum": 1,
    }
    if cond["smoking"] == "禁煙":
        params["smk"] = 1
    if cond["breakfast"] == "朝食あり":
        params["meal_type"] = 1

    url = "http://jws.jalan.net/APIAdvance/StockSearch/V1/"
    hotels = []
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            for h_el in root.findall(".//{*}Hotel"):
                h_name = h_el.findtext("{*}HotelName", "")
                h_id = h_el.findtext("{*}HotelID", "")
                h_url = h_el.findtext("{*}HotelDetailURL", "https://www.jalan.net/")
                img_url = h_el.findtext("{*}PictureURL", "")
                sample_rate = float(h_el.findtext("{*}SampleRate", "0.0") or 0.0)
                h_lat = float(h_el.findtext("{*}Y", "0") or 0)
                h_lon = float(h_el.findtext("{*}X", "0") or 0)

                dist_m = haversine_distance_meters(lat, lon, h_lat, h_lon)
                walk_min = calculate_walk_minutes(dist_m)

                min_price = None
                best_plan = "おすすめプラン"
                for p_el in h_el.findall(".//{*}Plan"):
                    price_str = p_el.findtext(".//{*}Price", "0")
                    price = int(price_str) if price_str.isdigit() else 0
                    if price > 0 and (min_price is None or price < min_price):
                        min_price = price
                        best_plan = p_el.findtext("{*}PlanName", best_plan)

                if min_price and min_price <= cond["budget_max"]:
                    hotels.append({
                        "hotel_id": f"jalan_{h_id}",
                        "hotel_name": h_name,
                        "price": min_price,
                        "walk_minutes": walk_min,
                        "review_rate": sample_rate,
                        "agent": "じゃらん",
                        "plan_name": best_plan,
                        "image_url": img_url,
                        "url": h_url,
                        "tags": [t for t in [cond["smoking"], cond["breakfast"] if cond["breakfast"] != "指定なし" else ""] if t],
                    })
    except Exception as e:
        logger.warning(f"じゃらんAPI取得エラー: {e}")

    return hotels


def get_mock_hotels(cond: Dict[str, Any]) -> List[Dict[str, Any]]:
    dest = cond.get("destination", "東京駅")
    cin_str = cond["checkin_date"]
    booked = cond.get("booked_price")

    templates = [
        {
            "name": f"スーパーホテルPremier{dest}八重洲口",
            "base_price": 9500 if not booked else min(booked - 2500, 9500),
            "walk": 4,
            "rate": 4.3,
            "agent": "楽天トラベル",
            "plan": "【公式限定・禁煙】天然温泉＆焼きたてパン朝食ビュッフェ付スマートSTAY",
            "image": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80",
            "url": f"https://travel.rakuten.co.jp/search?f_query={urllib.parse.quote(dest)}",
            "tags": ["禁煙", "天然温泉", "朝食無料"],
        },
        {
            "name": f"京王プレッソイン{dest}中央通り",
            "base_price": 10800 if not booked else min(booked - 1200, 10800),
            "walk": 6,
            "rate": 4.1,
            "agent": "じゃらん",
            "plan": "【直前割】シンプルステイ☆軽朝食無料サービス（禁煙シングル）",
            "image": "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=600&q=80",
            "url": f"https://www.jalan.net/uw/uwp3000/uww3001.do?keyword={urllib.parse.quote(dest)}",
            "tags": ["禁煙", "軽朝食付"],
        },
        {
            "name": f"アパホテル＆リゾート{dest}駅前",
            "base_price": 8900 if not booked else min(booked - 3100, 8900),
            "walk": 3,
            "rate": 4.0,
            "agent": "楽天トラベル",
            "plan": "【素泊まり】駅チカ直結！大浴殿・露天風呂無料利用プラン",
            "image": "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=600&q=80",
            "url": f"https://travel.rakuten.co.jp/search?f_query={urllib.parse.quote(dest)}",
            "tags": ["駅徒歩3分", "露天風呂付大浴場"],
        },
        {
            "name": f"ダイワロイネットホテル{dest}",
            "base_price": 12200,
            "walk": 7,
            "rate": 4.2,
            "agent": "じゃらん",
            "plan": "ゆったりワイドデスク＆高速Wi-Fiビジネス応援プラン（禁煙）",
            "image": "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?auto=format&fit=crop&w=600&q=80",
            "url": f"https://www.jalan.net/uw/uwp3000/uww3001.do?keyword={urllib.parse.quote(dest)}",
            "tags": ["禁煙", "広々デスク"],
        },
        {
            "name": f"三井ガーデンホテル{dest}前",
            "base_price": 13800,
            "walk": 5,
            "rate": 4.4,
            "agent": "楽天トラベル",
            "plan": "洗練のハイグレードシングル【大浴場・快眠ベッド完備】",
            "image": "https://images.unsplash.com/photo-1590490360182-c33d57733427?auto=format&fit=crop&w=600&q=80",
            "url": f"https://travel.rakuten.co.jp/search?f_query={urllib.parse.quote(dest)}",
            "tags": ["禁煙", "大浴場あり", "デザイナーズ"],
        },
    ]

    hotels = []
    for idx, t in enumerate(templates, start=1):
        price = t["base_price"]
        if price > cond["budget_max"]:
            continue
        if t["walk"] > cond["max_walk_minutes"]:
            continue
        if t["rate"] < cond["min_review_rate"]:
            continue

        hotels.append({
            "hotel_id": f"demo_{idx}",
            "hotel_name": t["name"],
            "price": price,
            "walk_minutes": t["walk"],
            "review_rate": t["rate"],
            "agent": t["agent"],
            "plan_name": t["plan"],
            "image_url": t["image"],
            "url": t["url"],
            "tags": t["tags"],
        })

    return hotels


# =====================================================================
# 4. 実行エンジン & 出力データ生成
# =====================================================================
def load_history() -> Dict[str, Any]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_history(history: Dict[str, Any]) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"履歴保存エラー: {e}")


def process_monitoring() -> Dict[str, Any]:
    geo_cache = load_geo_cache()
    conditions = load_conditions(CONDITIONS_FILE, geo_cache)
    save_geo_cache(geo_cache)

    history = load_history()
    now_iso = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    trip_results = []
    csv_rows = [
        "管理名,チェックイン日,目的地,最安料金,予約中価格,乗り換え推奨,最安ホテル名,予約サイト,徒歩分数,レビュー評価,プラン名,予約URL,最終更新"
    ]

    for cond in conditions:
        trip_id = cond["id"]
        mgmt = cond["management_name"]
        cin = cond["checkin_date"]
        dest = cond["destination"]
        booked_price = cond.get("booked_price")

        logger.info(f"[{mgmt}] 検索中: 行き先={dest}, 日程={cin}, 上限=¥{cond['budget_max']:,}")

        hotels = []
        if RAKUTEN_APP_ID:
            hotels.extend(fetch_rakuten_hotels(cond))
            time.sleep(1.0)  # 楽天APIレートリミット（1秒1リクエスト規約）遵守
        if JALAN_API_KEY:
            hotels.extend(fetch_jalan_hotels(cond))
            time.sleep(0.5)

        if not hotels and not RAKUTEN_APP_ID and not JALAN_API_KEY:
            hotels = get_mock_hotels(cond)

        matched = []
        for h in hotels:
            if h["price"] <= cond["budget_max"] and h["walk_minutes"] <= cond["max_walk_minutes"] and h["review_rate"] >= cond["min_review_rate"]:
                matched.append(h)

        matched.sort(key=lambda x: x["price"])

        decorated_hotels = []
        for rank, h in enumerate(matched, start=1):
            h_copy = dict(h)
            h_copy["rank"] = rank
            h_key = f"{trip_id}_{h['hotel_id']}_{cin}"
            curr_price = h["price"]

            prev_record = history.get(h_key, {})
            prev_price = prev_record.get("price", curr_price)
            price_diff = prev_price - curr_price

            h_copy["previous_price"] = prev_price
            h_copy["price_diff"] = price_diff

            if booked_price is not None:
                h_copy["cheaper_than_booked"] = curr_price < booked_price
                h_copy["saved_amount"] = (booked_price - curr_price) if curr_price < booked_price else 0
            else:
                h_copy["cheaper_than_booked"] = False
                h_copy["saved_amount"] = 0

            decorated_hotels.append(h_copy)
            history[h_key] = {"price": curr_price, "updated_at": now_iso}

        has_cheaper = False
        max_saved = 0
        cheaper_hotels = [h for h in decorated_hotels if h["cheaper_than_booked"]]
        if cheaper_hotels:
            has_cheaper = True
            max_saved = max(h["saved_amount"] for h in cheaper_hotels)

        if booked_price is not None:
            display_hotels = cheaper_hotels if cheaper_hotels else decorated_hotels[:5]
        else:
            display_hotels = decorated_hotels[:5]

        trip_data = {
            "id": trip_id,
            "management_name": mgmt,
            "checkin_date": cin,
            "stay_nights": cond["stay_nights"],
            "destination": dest,
            "booked_price": booked_price,
            "has_cheaper_option": has_cheaper,
            "max_saved_amount": max_saved,
            "cheaper_count": len(cheaper_hotels),
            "total_matched": len(decorated_hotels),
            "filters": {
                "smoking": cond["smoking"],
                "breakfast": cond["breakfast"],
                "max_walk": cond["max_walk_minutes"],
                "min_review": cond["min_review_rate"],
                "budget_max": cond["budget_max"],
            },
            "hotels": display_hotels,
        }
        trip_results.append(trip_data)

        if display_hotels:
            best = display_hotels[0]
            cheaper_flag = "○" if has_cheaper else "×"
            csv_rows.append(
                f'"{mgmt}","{cin}","{dest}",{best["price"]},{booked_price or "未予約"},{cheaper_flag},'
                f'"{best["hotel_name"]}","{best["agent"]}",{best["walk_minutes"]},{best["review_rate"]},'
                f'"{best["plan_name"]}","{best["url"]}","{now_iso}"'
            )

    save_history(history)

    os.makedirs("docs", exist_ok=True)
    web_data = {
        "generated_at": now_iso,
        "trips": trip_results,
    }
    with open(DOCS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(web_data, f, ensure_ascii=False, indent=2)
    logger.info(f"{DOCS_DATA_FILE} を出力しました。")

    with open(RESULT_CSV_FILE, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(csv_rows))
    logger.info(f"{RESULT_CSV_FILE} を出力しました。")

    return web_data


def main():
    logger.info("=== 宿泊価格監視バッチ 開始 ===")
    process_monitoring()
    logger.info("=== 宿泊価格監視バッチ 完了 ===")


if __name__ == "__main__":
    main()
