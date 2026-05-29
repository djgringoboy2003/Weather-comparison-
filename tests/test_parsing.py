import unittest

from weather_compare.sources import parse_met_no, parse_open_meteo_daily


class OpenMeteoParsing(unittest.TestCase):
    def test_bare_keys(self):
        data = {
            "daily": {
                "time": ["2026-05-29", "2026-05-30"],
                "temperature_2m_max": [21.0, 23.5],
                "temperature_2m_min": [11.0, 12.0],
                "precipitation_sum": [0.0, 4.2],
                "precipitation_probability_max": [5, 70],
                "wind_speed_10m_max": [12.0, 18.0],
                "weather_code": [1, 61],
            }
        }
        days = parse_open_meteo_daily(data, "gfs_seamless")
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0].temp_max, 21.0)
        self.assertEqual(days[1].precip_mm, 4.2)
        self.assertEqual(days[1].weather_code, 61)

    def test_suffixed_keys(self):
        # Open-Meteo may append the model id to each variable.
        data = {
            "daily": {
                "time": ["2026-05-29"],
                "temperature_2m_max_ecmwf_ifs04": [20.0],
                "temperature_2m_min_ecmwf_ifs04": [10.0],
                "precipitation_sum_ecmwf_ifs04": [1.0],
                "weather_code_ecmwf_ifs04": [3],
            }
        }
        days = parse_open_meteo_daily(data, "ecmwf_ifs04")
        self.assertEqual(days[0].temp_max, 20.0)
        self.assertEqual(days[0].weather_code, 3)
        self.assertIsNone(days[0].precip_prob)  # missing column -> None

    def test_ragged_columns(self):
        data = {
            "daily": {
                "time": ["2026-05-29", "2026-05-30"],
                "temperature_2m_max": [20.0],  # short
            }
        }
        days = parse_open_meteo_daily(data, "icon_seamless")
        self.assertEqual(days[0].temp_max, 20.0)
        self.assertIsNone(days[1].temp_max)


class MetNoParsing(unittest.TestCase):
    def test_daily_aggregation_utc(self):
        data = {
            "properties": {
                "timeseries": [
                    {
                        "time": "2026-05-29T06:00:00Z",
                        "data": {
                            "instant": {"details": {"air_temperature": 10.0}},
                            "next_1_hours": {
                                "summary": {"symbol_code": "cloudy"},
                                "details": {"precipitation_amount": 0.0},
                            },
                        },
                    },
                    {
                        "time": "2026-05-29T12:00:00Z",
                        "data": {
                            "instant": {"details": {"air_temperature": 18.0}},
                            "next_1_hours": {
                                "summary": {"symbol_code": "lightrain_day"},
                                "details": {"precipitation_amount": 1.5},
                            },
                        },
                    },
                    {
                        "time": "2026-05-30T12:00:00Z",
                        "data": {
                            "instant": {"details": {"air_temperature": 22.0}},
                            "next_1_hours": {"details": {"precipitation_amount": 0.0}},
                        },
                    },
                ]
            }
        }
        days = parse_met_no(data, "auto", 7)  # auto -> group by UTC date
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0].date, "2026-05-29")
        self.assertEqual(days[0].temp_max, 18.0)
        self.assertEqual(days[0].temp_min, 10.0)
        self.assertEqual(days[0].precip_mm, 1.5)
        self.assertEqual(days[0].weather_code, 61)  # lightrain -> 61


if __name__ == "__main__":
    unittest.main()
