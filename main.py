import os
import json
import math
import time
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import re
import unicodedata
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



# 主要コンベンション施設・有名ランドマーク事前登録（ジオコーディングの揺れ・誤判定防止用）
BUILTIN_LANDMARKS: Dict[str, Tuple[float, float]] = {
    "名古屋コンベンションホール": (35.16196, 136.88287),
    "名古屋コンベンションセンター": (35.16196, 136.88287),
    "グローバルゲート": (35.16196, 136.88287),
    "ささしまライブ": (35.16167, 136.88162),
    "名古屋国際会議場": (35.13159, 136.89698),
    "センチュリーホール": (35.13159, 136.89698),
    "ウインクあいち": (35.17068, 136.88641),
    "愛知県産業労働センター": (35.17068, 136.88641),
    "ポートメッセなごや": (35.04789, 136.84429),
    "名古屋市国際展示場": (35.04789, 136.84429),
    "吹上ホール": (35.15823, 136.92970),
    "愛知県国際展示場": (34.86250, 136.81880),
    "Aichi Sky Expo": (34.86250, 136.81880),
    "東京ビッグサイト": (35.63205, 139.79780),
    "東京国際展示場": (35.63205, 139.79780),
    "幕張メッセ": (35.64730, 140.03465),
    "パシフィコ横浜": (35.45999, 139.63533),
    "東京国際フォーラム": (35.67679, 139.76354),
    "インテックス大阪": (34.63726, 135.42001),
    "グランキューブ大阪": (34.68939, 135.48627),
    "大阪府立国際会議場": (34.68939, 135.48627),
    "神戸ポートピアホテル": (34.66522, 135.21370),
    "ワールド記念ホール": (34.66398, 135.21014),
    "福岡国際会議場": (33.60432, 130.40354),
    "マリンメッセ福岡": (33.60728, 130.40210),
    "国立京都国際会館": (35.06108, 135.78315),
    "アクリエひめじ": (34.82663, 134.69990),
    # 宝飾・ジュエリー集積地・問屋街・主要店舗
    "御徒町": (35.70744, 139.77472),
    "御徒町駅": (35.70744, 139.77472),
    "甲府": (35.66723, 138.56906),
    "甲府駅": (35.66723, 138.56906),
    "銀座": (35.67198, 139.76396),
    "心斎橋": (35.67498, 135.50028),
    "南船場": (34.67667, 135.50194),
    "ミキモト 銀座": (35.67180, 139.76510),
    "TASAKI 銀座": (35.67120, 139.76450),
    "田中貴金属 銀座": (35.67200, 139.76530),

    # 呉服・きもの集積地・問屋街
    "京都室町": (35.00762, 135.75841),
    "室町通": (35.00762, 135.75841),
    "四条烏丸": (35.00373, 135.75924),
    "烏丸": (35.00373, 135.75924),
    "日本橋": (35.68117, 139.77447),
    "十日町": (37.13333, 138.75667),

    # 主要百貨店（宝飾サロン・特選呉服サロン）
    "日本橋三越": (35.68593, 139.77334),
    "伊勢丹新宿": (35.69170, 139.70462),
    "銀座三越": (35.67137, 139.76562),
    "松屋銀座": (35.67252, 139.76673),
    "松坂屋名古屋": (35.16544, 136.90793),
    "ジェイアール名古屋タカシマヤ": (35.17091, 136.88291),
    "阪急うめだ": (34.70255, 135.49867),
    "髙島屋大阪": (34.66487, 135.50117),
    "大丸心斎橋": (34.67389, 135.50089),
    "岩田屋": (33.58988, 130.39868),
    "大丸福岡天神": (33.58882, 130.40134),
    "浜松駅": (34.70375, 137.73466),
    # 宝飾・きもの主要チェーン・業界拠点（正確な番地座標）
    "京都きもの友禅": (35.68364, 139.78201),
    "京都きもの友禅 本社": (35.68364, 139.78201),
    "京都きもの友禅 日本橋": (35.68364, 139.78201),
    "京都きもの友禅 新宿": (35.68877, 139.69663),
    "京都きもの友禅 名古屋": (35.16912, 136.90582),
    "さが美": (35.45509, 139.63145),
    "きもの さが美": (35.45509, 139.63145),
    "さが美 本社": (35.45509, 139.63145),
    "カンダキラット": (35.70615, 139.77663),
    "KIRAT": (35.70615, 139.77663),
    "キラット": (35.70615, 139.77663),
    "菅田": (35.70615, 139.77663),
    "ジュエリーツツミ": (35.67193, 139.76367),
    "ツツミ": (35.67193, 139.76367),
    "エステール": (35.66440, 139.74391),
    "As-meエステール": (35.66440, 139.74391),
    "十日町駅": (37.13333, 138.75667),
}



def normalize_address(text: str) -> str:
    if not text:
        return ""
    # 1. 全角英数・カナ・記号の半角正規化
    norm = unicodedata.normalize('NFKC', text.strip())
    # 2. 郵便番号（〒XXX-XXXX）の除去
    norm = re.sub(r'〒\s*\d{3}-?\d{4}', '', norm).strip()
    # 3. 各種Unicodeハイフン・ダッシュ・長音の統一
    norm = re.sub(r'[ー―—−˗֊‐‑‒–—―⁻₋−]', '-', norm)
    # 4. 連続空白の単一化
    norm = re.sub(r'[\s　]+', ' ', norm).strip()
    return norm


def clean_address_hierarchy(address: str) -> str:
    # ビル名、階数、部屋番号等の末尾テキストを除去して番地までを抽出
    # 例: "愛知県名古屋市中区栄3-16-1 松坂屋本館8F" -> "愛知県名古屋市中区栄3-16-1"
    m = re.match(r'^((?:[^\s]{2,3}[都道府県])?[^\s]+[市区町村][^\s]+?\d+(?:-\d+)*)(?:\s+.*)?$', address)
    if m:
        return m.group(1).strip()
    return address

def smart_extract_location(query: str) -> Optional[str]:
    # 小売店名・呉服店名＋住所/駅名から、測位可能な地名・駅名を抽出
    # 1. カッコ内の住所・駅名（例: 山田宝飾（名古屋市中区栄））
    m_par = re.search(r'[（\(](.*?)[）\)]', query)
    if m_par:
        sub = m_par.group(1).strip()
        if sub:
            return sub

    # 2. 空白区切りで後半に駅名・市区町村がある場合（例: 辻呉服店 浜松市中区）
    parts = re.split(r'[\s　]+', query.strip())
    if len(parts) >= 2:
        for p in parts[1:]:
            if any(p.endswith(s) for s in ['駅', '市', '区', '町', '村', '通', '条', '丁目']) or any(s in p for s in ['都', '道', '府', '県']):
                return p

    # 3. 住所文字列の抽出（例: 静岡市葵区、甲府市丸の内）
    m_addr = re.search(r'((?:[^\s]{2,3}[都道府県])?[^\s]+[市区町村][^\s]*)', query)
    if m_addr:
        return m_addr.group(1)

    # 4. 駅名パターンの抽出（例: 宝石のタナカ 郡山駅前）
    m_st = re.search(r'([^\s]+駅)', query)
    if m_st:
        return m_st.group(1)

    return None

def geocode_destination(destination: str, geo_cache: Dict[str, Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    raw_dest = (destination or "").strip()
    if not raw_dest:
        return None

    # 住所・全角文字・郵便番号の正規化
    norm_dest = normalize_address(raw_dest)
    clean_addr = clean_address_hierarchy(norm_dest)

    # キャッシュチェック
    for key in [clean_addr, norm_dest, raw_dest]:
        if key in geo_cache:
            c = geo_cache[key]
            if isinstance(c, dict) and c.get("v") == GEO_CACHE_VERSION and "lat" in c and "lon" in c:
                return float(c["lat"]), float(c["lon"])

    # 0. 主要ランドマーク辞書の事前チェック（完全一致優先）
    if norm_dest in BUILTIN_LANDMARKS:
        coords = BUILTIN_LANDMARKS[norm_dest]
        geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": coords[0], "lon": coords[1]}
        return coords
    # 部分一致（最低3文字以上の検索語で、かつランドマーク名に検索語が含まれる場合のみ）
    if len(norm_dest) >= 3:
        for lm_name, coords in BUILTIN_LANDMARKS.items():
            if norm_dest in lm_name:
                geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": coords[0], "lon": coords[1]}
                return coords

    # 1. 国土地理院（日本全国の番地・丁目・街区を完全カバー）
    # 候補順: ①番地までクリーニングした住所 -> ②正規化文字列
    for candidate in list(dict.fromkeys([clean_addr, norm_dest])):
        try:
            url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={urllib.parse.quote(candidate)}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0 and "geometry" in data[0]:
                    title = data[0].get("properties", {}).get("title", "")
                    # 「名古屋」を含むのに千葉県成田市名古屋等の別県同名地名が当たる誤爆を防止
                    if "名古屋" in candidate and "愛知" not in title and "名古屋" not in title:
                        continue
                    coords = data[0]["geometry"].get("coordinates", [])
                    if len(coords) >= 2:
                        lon, lat = float(coords[0]), float(coords[1])
                        geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": lat, "lon": lon}
                        return lat, lon
        except Exception as e:
            logger.debug(f"国土地理院測位失敗 ({candidate}): {e}")

    # 2. 国土地理院 番地段階的フォールバック（最新の番地が未収録の場合に丁目・町名へフォールバック）
    fallback_addr = clean_addr
    while re.search(r'-\d+$', fallback_addr):
        fallback_addr = re.sub(r'-\d+$', '', fallback_addr)
        try:
            url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={urllib.parse.quote(fallback_addr)}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0 and "geometry" in data[0]:
                    coords = data[0]["geometry"].get("coordinates", [])
                    if len(coords) >= 2:
                        lon, lat = float(coords[0]), float(coords[1])
                        geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": lat, "lon": lon}
                        return lat, lon
        except Exception:
            pass

    # 3. OpenStreetMap Nominatim
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(clean_addr)}&format=json&countrycodes=jp"
        headers = {"User-Agent": "HotelMonitorApp/2.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": lat, "lon": lon}
                return lat, lon
    except Exception as e:
        logger.debug(f"Nominatimジオコーディング失敗 ({clean_addr}): {e}")

    # 4. 小売店舗・呉服店向けスマートフォールバック（店舗名から住所・駅名を抽出して測位）
    sub_loc = smart_extract_location(norm_dest)
    if sub_loc and sub_loc != norm_dest and sub_loc != raw_dest:
        logger.info(f"店舗名から測位対象地名を抽出: '{raw_dest}' -> '{sub_loc}'")
        res = geocode_destination(sub_loc, geo_cache)
        if res:
            geo_cache[raw_dest] = {"v": GEO_CACHE_VERSION, "lat": res[0], "lon": res[1]}
            return res

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
def _safe_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(str(val).replace(",", "").replace("，", "").strip())
    except (ValueError, TypeError):
        logger.warning(f"数値変換失敗: '{val}' → Noneとして処理")
        return None

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
                "stay_nights": max(1, int(item.get("stay_nights", 1))),
                "adult_num": int(item.get("adult_num", 1)),
                "destination": dest,
                "target_lat": float(target_lat) if target_lat else None,
                "target_lon": float(target_lon) if target_lon else None,
                "max_walk_minutes": int(item.get("max_walk_minutes", 15)),
                "budget_max": int(item.get("budget_max", 15000)),
                "booked_price": _safe_int(item.get("booked_price")),
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
                review_rate = float(basic.get("reviewAverage", 0.0) or 0.0)

                # Extract actual room prices from VacantHotelSearch response
                min_room_price = None
                best_plan_name = basic.get("hotelSpecial", "おすすめ宿泊プラン")
                for info_item in h_info[1:]:
                    room_info = info_item.get("roomInfo", [])
                    if not room_info:
                        continue
                    curr_plan = best_plan_name
                    curr_charge = None
                    for ri in room_info:
                        if "roomBasicInfo" in ri:
                            curr_plan = ri["roomBasicInfo"].get("planName", curr_plan)
                        if "dailyCharge" in ri:
                            dc = ri["dailyCharge"]
                            c = dc.get("total") or dc.get("price") or dc.get("charge")
                            if c:
                                try:
                                    curr_charge = int(c)
                                except (ValueError, TypeError):
                                    pass
                    if curr_charge and curr_charge > 0:
                        if min_room_price is None or curr_charge < min_room_price:
                            min_room_price = curr_charge
                            best_plan_name = curr_plan

                # Fallback to hotelMinCharge if no room prices found
                try:
                    min_charge = int(basic.get("hotelMinCharge", 0))
                except (ValueError, TypeError):
                    min_charge = 0
                price = min_room_price if (min_room_price and min_room_price > 0) else min_charge

                hotels.append({
                    "hotel_id": f"rakuten_{basic.get('hotelNo')}",
                    "hotel_name": basic.get("hotelName", ""),
                    "price": price,
                    "price_is_total": False,
                    "walk_minutes": walk_min,
                    "review_rate": review_rate,
                    "agent": "楽天トラベル",
                    "plan_name": best_plan_name,
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
        "y": lat,
        "x": lon,
        "l_rad": round(radius_km, 1),
        "datum": 1,
    }
    if cond["smoking"] == "禁煙":
        params["smk"] = 1
    if cond["breakfast"] == "朝食あり":
        params["meal_type"] = 1

    url = "https://jws.jalan.net/APIAdvance/StockSearch/V1/"
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
                    price_str = (
                        p_el.findtext(".//{*}SampleRate")
                        or p_el.findtext(".//{*}PlanPrice")
                        or p_el.findtext(".//{*}Price")
                        or p_el.findtext(".//{*}TotalRate")
                        or ""
                    )
                    digits = re.sub(r'[^\d]', '', price_str)
                    price = int(digits) if digits else 0
                    if price > 0 and (min_price is None or price < min_price):
                        min_price = price
                        best_plan = p_el.findtext("{*}PlanName", best_plan)

                # Fallback to Hotel SampleRate if no plans
                if min_price is None or min_price <= 0:
                    h_sample = h_el.findtext("{*}SampleRate", "")
                    digits = re.sub(r'[^\d]', '', h_sample)
                    min_price = int(digits) if digits else 0

                if min_price and min_price <= cond["budget_max"]:
                    hotels.append({
                        "hotel_id": f"jalan_{h_id}",
                        "hotel_name": h_name,
                        "price": min_price,
                        "price_is_total": True,
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
    nights = max(1, int(cond.get("stay_nights", 1)))
    # 予約中価格が全泊総額のため、1泊あたりの基準額を算出
    booked_per_night = (booked // nights) if booked else None

    templates = [
        {
            "name": f"スーパーホテルPremier{dest}八重洲口",
            "base_price": 9500 if not booked_per_night else min(booked_per_night - 2500, 9500),
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
            "base_price": 10800 if not booked else min(booked_per_night - 1200, 10800),
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
            "base_price": 8900 if not booked else min(booked_per_night - 3100, 8900),
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
            "price_is_total": False,
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


ALLOW_MOCK_DATA = os.environ.get("ALLOW_MOCK_DATA", "false").lower() in ("true", "1")


def process_monitoring() -> Dict[str, Any]:
    geo_cache = load_geo_cache()
    conditions = load_conditions(CONDITIONS_FILE, geo_cache)
    save_geo_cache(geo_cache)

    history = load_history()
    now_iso = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    trip_results = []
    csv_rows = [
        "管理名,チェックイン日,宿泊数,目的地,最安料金(1泊),宿泊総額(全泊分),予約中総額,差額(安価分),最安ホテル名,予約サイト,徒歩分数,レビュー評価,プラン名,予約URL,最終更新"
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
            if ALLOW_MOCK_DATA:
                logger.info(f"[{mgmt}] テスト環境用モックデータを使用します。")
                hotels = get_mock_hotels(cond)
            else:
                logger.info(f"[{mgmt}] APIキーが設定されていないため、検索結果は空(0件)として出力します（デモデータは出力しません）。")
                hotels = []

        matched = []
        nights = max(1, int(cond.get("stay_nights", 1)))
        for h in hotels:
            per_night = h["price"] / nights if h.get("price_is_total") else h["price"]
            if per_night <= cond["budget_max"] and h["walk_minutes"] <= cond["max_walk_minutes"] and h["review_rate"] >= cond["min_review_rate"]:
                matched.append(h)

        matched.sort(key=lambda x: x["price"])

        decorated_hotels = []
        for rank, h in enumerate(matched, start=1):
            h_copy = dict(h)
            h_copy["rank"] = rank
            h_key = f"{trip_id}_{h['hotel_id']}_{cin}"
            curr_price = h["price"]
            if h.get("price_is_total"):
                total_price = curr_price
            else:
                total_price = curr_price * nights
            h_copy["total_price"] = total_price

            prev_record = history.get(h_key, {})
            prev_price = prev_record.get("price", curr_price)
            price_diff = prev_price - curr_price

            h_copy["previous_price"] = prev_price
            h_copy["price_diff"] = price_diff

            if booked_price is not None:
                # 予約済み価格は全泊分の総額として比較
                h_copy["cheaper_than_booked"] = total_price < booked_price
                h_copy["saved_amount"] = (booked_price - total_price) if total_price < booked_price else 0
            else:
                h_copy["cheaper_than_booked"] = False
                h_copy["saved_amount"] = 0

            decorated_hotels.append(h_copy)
            history[h_key] = {"price": curr_price, "total_price": total_price, "updated_at": now_iso}

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
            "adult_num": cond.get("adult_num", 1),
            "destination": dest,
            "target_lat": cond.get("target_lat"),
            "target_lon": cond.get("target_lon"),
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
            best_total = best.get("total_price", best["price"] * nights)
            per_night_price = best_total // nights if nights > 0 else best["price"]
            saved = best["saved_amount"] if best.get("cheaper_than_booked") else 0
            csv_rows.append(
                f'"{ mgmt}","{cin}",{nights},"{dest}",{per_night_price},{best_total},'
                f'{booked_price if booked_price is not None else "未予約"},{saved},'
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
