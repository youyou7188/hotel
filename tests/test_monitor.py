import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
    JST,
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
        today = datetime.now(JST)
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        future1_str = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        future2_str = (today + timedelta(days=20)).strftime("%Y-%m-%d")

        raw_conditions = [
            {
                "id": "trip-bulk",
                "management_name": "東京複数出張",
                "checkin_date": f"{future1_str}, {future2_str}, {yesterday_str}",
                "destination": "東京駅",
                "budget_max": 15000,
                "status": "監視中",
            },
            {
                "id": "trip-finished",
                "management_name": "終了出張",
                "checkin_date": future1_str,
                "destination": "大阪駅",
                "status": "終了",
            },
        ]

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as tmp:
            json.dump(raw_conditions, tmp)
            tmp_path = tmp.name

        try:
            conds = load_conditions(tmp_path)
            self.assertEqual(len(conds), 2)
            self.assertEqual(conds[0]["checkin_date"], future1_str)
            self.assertEqual(conds[1]["checkin_date"], future2_str)
            self.assertEqual(conds[0]["destination"], "東京駅")
        finally:
            os.remove(tmp_path)

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

        # 2nd call should use cache
        coords2 = geocode_destination("東京駅", cache)
        self.assertEqual(coords2, (35.681, 139.767))
        mock_get.assert_called_once()

    def test_get_mock_hotels_with_booked_comparison(self):
        cond = {
            "destination": "東京駅",
            "checkin_date": "2026-10-15",
            "booked_price": 12000,
            "stay_nights": 1,
            "budget_max": 16000,
            "max_walk_minutes": 10,
            "min_review_rate": 3.8,
        }

        hotels = get_mock_hotels(cond)
        self.assertTrue(len(hotels) >= 3)
        for h in hotels:
            self.assertIn("hotel_name", h)
            self.assertIn("price", h)
            self.assertIn("price_is_total", h)
            self.assertTrue(h["price"] > 0)  # No negative prices
            self.assertTrue(h["walk_minutes"] <= 10)
            self.assertTrue(h["price"] <= 16000)

    def test_multiple_nights_total_price_comparison(self):
        cond = {
            "id": "test-2nights",
            "management_name": "2泊出張テスト",
            "destination": "東京駅",
            "checkin_date": "2026-10-15",
            "stay_nights": 2,
            "booked_price": 22000,
            "budget_max": 16000,
            "max_walk_minutes": 10,
            "min_review_rate": 3.8,
        }
        hotels = get_mock_hotels(cond)
        self.assertTrue(len(hotels) >= 1)
        for h in hotels:
            self.assertIn("price", h)
            self.assertTrue(h["price"] > 0)  # No negative prices
            total = h["price"] * cond["stay_nights"]
            self.assertTrue(total > 0)

    @patch("main.RAKUTEN_APP_ID", "")
    @patch("main.JALAN_API_KEY", "")
    def test_process_monitoring_end_to_end(self):
        """Test with temp files to avoid overwriting production data."""
        import main as m
        original_docs = m.DOCS_DATA_FILE
        original_csv = m.RESULT_CSV_FILE
        original_history = m.HISTORY_FILE
        original_conditions = m.CONDITIONS_FILE
        original_geocache = m.GEO_CACHE_FILE

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create temp conditions file
            today = datetime.now(JST)
            future_str = (today + timedelta(days=14)).strftime("%Y-%m-%d")
            conditions = [
                {
                    "id": "test-trip",
                    "management_name": "テスト出張",
                    "checkin_date": future_str,
                    "destination": "東京駅",
                    "budget_max": 15000,
                    "booked_price": 12000,
                    "status": "監視中",
                }
            ]
            cond_path = os.path.join(tmpdir, "conditions.json")
            with open(cond_path, "w", encoding="utf-8") as f:
                json.dump(conditions, f)

            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir, exist_ok=True)

            try:
                m.CONDITIONS_FILE = cond_path
                m.DOCS_DATA_FILE = os.path.join(docs_dir, "data.json")
                m.RESULT_CSV_FILE = os.path.join(tmpdir, "result.csv")
                m.HISTORY_FILE = os.path.join(tmpdir, "history.json")
                m.GEO_CACHE_FILE = os.path.join(tmpdir, ".geo_cache.json")
                m.ALLOW_MOCK_DATA = True

                web_data = process_monitoring()
                self.assertIn("generated_at", web_data)
                self.assertIn("trips", web_data)
                self.assertTrue(len(web_data["trips"]) >= 1)

                test_trip = web_data["trips"][0]
                self.assertEqual(test_trip["booked_price"], 12000)
                self.assertTrue(test_trip["has_cheaper_option"])
                self.assertTrue(test_trip["max_saved_amount"] > 0)

                self.assertTrue(os.path.exists(m.DOCS_DATA_FILE))
                self.assertTrue(os.path.exists(m.RESULT_CSV_FILE))
            finally:
                m.CONDITIONS_FILE = original_conditions
                m.DOCS_DATA_FILE = original_docs
                m.RESULT_CSV_FILE = original_csv
                m.HISTORY_FILE = original_history
                m.GEO_CACHE_FILE = original_geocache

    def test_smart_extract_and_retail_store_resolution(self):
        from main import smart_extract_location
        self.assertEqual(smart_extract_location("山田宝飾（名古屋市中区栄）"), "名古屋市中区栄")
        self.assertEqual(smart_extract_location("きもの辻 浜松市中区"), "浜松市中区")
        self.assertEqual(smart_extract_location("ジュエリー田中 郡山駅前"), "郡山駅")

    @patch("main.requests.get")
    def test_direct_address_normalization_and_geocoding(self, mock_get):
        from main import normalize_address, clean_address_hierarchy, geocode_destination
        norm = normalize_address("〒460-0008　愛知県名古屋市中区栄３丁目１６−１　松坂屋名古屋店本館8F")
        clean = clean_address_hierarchy(norm)
        self.assertEqual(clean, "愛知県名古屋市中区栄3丁目16-1")

        # Mock GSI API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "geometry": {"coordinates": [136.907, 35.165]},
                "properties": {"title": "愛知県名古屋市中区栄3丁目16"}
            }
        ]
        mock_get.return_value = mock_resp

        cache = {}
        coords = geocode_destination("愛知県名古屋市中区栄3-16-1", cache)
        self.assertIsNotNone(coords)
        self.assertAlmostEqual(coords[0], 35.165, places=2)
        self.assertAlmostEqual(coords[1], 136.907, places=2)

    def test_safe_int_conversion(self):
        from main import _safe_int
        self.assertEqual(_safe_int(12000), 12000)
        self.assertEqual(_safe_int("12000"), 12000)
        self.assertEqual(_safe_int("15,000"), 15000)
        self.assertIsNone(_safe_int(None))
        self.assertIsNone(_safe_int(""))
        self.assertIsNone(_safe_int("null"))
        self.assertIsNone(_safe_int("未予約"))

    def test_stay_nights_minimum_validation(self):
        today = datetime.now(JST)
        future_str = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        raw_conditions = [
            {
                "management_name": "0泊テスト",
                "checkin_date": future_str,
                "destination": "東京駅",
                "stay_nights": 0,
                "status": "監視中",
            }
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as tmp:
            json.dump(raw_conditions, tmp)
            tmp_path = tmp.name
        try:
            conds = load_conditions(tmp_path)
            self.assertEqual(len(conds), 1)
            self.assertEqual(conds[0]["stay_nights"], 1)  # Should be clamped to 1
        finally:
            os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
