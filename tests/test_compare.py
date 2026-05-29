import unittest

from weather_compare.compare import build_consensus
from weather_compare.model import DayForecast, SourceForecast


def src(name, weight, *days):
    return SourceForecast(name=name, model_id=name, weight=weight, days=list(days))


class ConsensusLogic(unittest.TestCase):
    def test_weighted_mean_and_spread(self):
        a = src("A", 1.0, DayForecast("2026-05-29", temp_max=20.0, temp_min=10.0))
        b = src("B", 3.0, DayForecast("2026-05-29", temp_max=24.0, temp_min=12.0))
        [day] = build_consensus([a, b])
        # Weighted toward B (weight 3): (20*1 + 24*3)/4 = 23.0
        self.assertEqual(day.temp_max, 23.0)
        self.assertEqual(day.temp_max_spread, 4.0)
        self.assertEqual(day.n_sources, 2)

    def test_high_confidence_when_sources_agree(self):
        days = [
            src(f"S{i}", 1.0, DayForecast("2026-05-29", temp_max=20.0 + i * 0.3,
                                          precip_mm=0.0))
            for i in range(4)
        ]
        [day] = build_consensus(days)
        self.assertEqual(day.confidence, "High")
        self.assertEqual(day.precip_chance, 0)

    def test_low_confidence_on_wide_spread(self):
        a = src("A", 1.0, DayForecast("2026-05-29", temp_max=15.0, precip_mm=0.0))
        b = src("B", 1.0, DayForecast("2026-05-29", temp_max=25.0, precip_mm=10.0))
        [day] = build_consensus([a, b])
        self.assertEqual(day.confidence, "Low")  # 10C spread + split wet/dry

    def test_single_source_is_low_confidence(self):
        a = src("A", 1.0, DayForecast("2026-05-29", temp_max=20.0, precip_mm=0.0))
        [day] = build_consensus([a])
        self.assertEqual(day.confidence, "Low")
        self.assertEqual(day.temp_max_spread, 0.0)

    def test_precip_chance_blends_flags_and_probability(self):
        # 3 of 4 sources wet -> 75% by flags; PoP avg 50 -> blended 62.
        days = [
            src("A", 1.0, DayForecast("2026-05-29", temp_max=20, precip_mm=2.0, precip_prob=40)),
            src("B", 1.0, DayForecast("2026-05-29", temp_max=20, precip_mm=2.0, precip_prob=60)),
            src("C", 1.0, DayForecast("2026-05-29", temp_max=20, precip_mm=2.0)),
            src("D", 1.0, DayForecast("2026-05-29", temp_max=20, precip_mm=0.0)),
        ]
        [day] = build_consensus(days)
        self.assertEqual(day.precip_chance, 62)

    def test_condition_follows_strong_precip_signal(self):
        # Sources code it "clear" but everyone predicts heavy rain.
        days = [
            src("A", 1.0, DayForecast("2026-05-29", temp_max=18, precip_mm=8.0,
                                      precip_prob=90, weather_code=1)),
            src("B", 1.0, DayForecast("2026-05-29", temp_max=18, precip_mm=9.0,
                                      precip_prob=85, weather_code=1)),
        ]
        [day] = build_consensus(days)
        self.assertEqual(day.condition_category, "rain")

    def test_dates_aligned_across_sources(self):
        a = src("A", 1.0,
                DayForecast("2026-05-29", temp_max=20.0),
                DayForecast("2026-05-30", temp_max=21.0))
        b = src("B", 1.0,
                DayForecast("2026-05-30", temp_max=23.0),
                DayForecast("2026-05-31", temp_max=24.0))
        out = build_consensus([a, b])
        self.assertEqual([d.date for d in out],
                         ["2026-05-29", "2026-05-30", "2026-05-31"])
        # The shared day combines both sources.
        self.assertEqual(out[1].n_sources, 2)
        self.assertEqual(out[1].temp_max, 22.0)


if __name__ == "__main__":
    unittest.main()
