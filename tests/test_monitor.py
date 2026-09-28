import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from main import (
    calculate_walk_minutes,
    geocode_destination,
    get_mock_hotels,
    haversine_distance_meters,
    load_conditions,
    process_monitoring,
    DOCS_DATA_FILE,
    RESULT_CSV_FILE,
)


class TestHotelMonitorV2(unittest.TestCase):
    def test_haversine_and_walk(self):
        lat1, lon1 = 35.6809591, 139.7673068
        lat2, lon2 = 35.6820000, 139.7690000
        dist = haversine_distance_meters(lat1, lon1, lat2, lon2)
        self.assertTrue(150 < dist < 350)
        walk_min = calculate_walk_minutes(dist)
        self.assertEqual(walk_min, math.ceil(dist / 80.0))

    def test_load_conditions_bulk_dates_and_past_filter(self):
        today = datetime.now()
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        future1_str = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        future2_str = (today + timedelta(days=20)).strftime("%Y-%m-%d")

        raw_conditions = [
            {
                "id": "trip-bulk",
                "management_name": "東京複数出張",
                "checkin_date": f"{future1_str}, {future2_str}, {yesterday_str}",  # まとめ打ち（未来2件＋過去1件）
                "destination": "東京駅",
                "budget_max": 15000,
                "status": "監視中",
            },
            {
                "id": "trip-finished",
                "management_name": "終了出張",
                "checkin_date": future1_str,
                "destination": "大阪駅",
                "status": "終了",  # 終了扱い
            },
        ]

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as tmp:
            json.dump(raw_conditions, tmp)
            tmp_path = tmp.name

        conds = load_conditions(tmp_path)
        os.remove(tmp_path)

        # 過去日程と終了案件が除外され、未来の2件のみ展開されること
        self.assertEqual(len(conds), 2)
        self.assertEqual(conds[0]["checkin_date"], future1_str)
        self.assertEqual(conds[1]["checkin_date"], future2_str)
        self.assertEqual(conds[0]["destination"], "東京駅")

    @patch("main.requests.get")
    def test_geocode_destination(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"geometry": {"coordinates": [139.767, 35.681]}}]
        mock_get.return_value = mock_resp

        cache = {}
        coords = geocode_destination("東京駅", cache)
        self.assertIsNotNone(coords)
        self.assertEqual(coords, (35.681, 139.767))
        self.assertIn("東京駅", cache)

        # 2回目はキャッシュから即時取得（APIは呼ばれない）
        coords2 = geocode_destination("東京駅", cache)
        self.assertEqual(coords2, (35.681, 139.767))
        mock_get.assert_called_once()

    def test_get_mock_hotels_with_booked_comparison(self):
        cond = {
            "destination": "東京駅",
            "checkin_date": "2026-10-15",
            "booked_price": 12000,  # 12,000円キープ中
            "budget_max": 16000,
            "max_walk_minutes": 10,
            "min_review_rate": 3.8,
        }

        hotels = get_mock_hotels(cond)
        self.assertTrue(len(hotels) >= 3)

        # ホテル情報の整合性チェック
        for h in hotels:
            self.assertIn("hotel_name", h)
            self.assertIn("price", h)
            self.assertIn("walk_minutes", h)
            self.assertIn("review_rate", h)
            self.assertIn("url", h)
            self.assertTrue(h["walk_minutes"] <= 10)
            self.assertTrue(h["price"] <= 16000)

    def test_process_monitoring_end_to_end(self):
        web_data = process_monitoring()
        self.assertIn("generated_at", web_data)
        self.assertIn("trips", web_data)
        self.assertTrue(len(web_data["trips"]) >= 1)

        # 東京出張（booked_price=12000）の乗り換え判定チェック
        tokyo_trip = next((t for t in web_data["trips"] if "東京" in t["management_name"]), None)
        self.assertIsNotNone(tokyo_trip)
        self.assertEqual(tokyo_trip["booked_price"], 12000)
        self.assertTrue(tokyo_trip["has_cheaper_option"])
        self.assertTrue(tokyo_trip["max_saved_amount"] > 0)

        # 生成ファイルの存在確認
        self.assertTrue(os.path.exists(DOCS_DATA_FILE))
        self.assertTrue(os.path.exists(RESULT_CSV_FILE))


if __name__ == "__main__":
    unittest.main()
