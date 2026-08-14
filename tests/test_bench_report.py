"""Tests for benchmarks/results/make_report.py (report generator)."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.results import make_report as mr

REPO = Path(__file__).resolve().parents[1]


def _scenario(**over):
    base = {
        "concurrence": 8, "requetes": 100, "duree_s": 10.0, "rps": 10.0,
        "lat_min_ms": 1.0, "lat_moy_ms": 5.0, "lat_max_ms": 20.0,
        "p50_ms": 4.0, "p95_ms": 9.0, "p99_ms": 15.0, "succes": 100,
        "erreurs": 0, "taux_erreur_pct": 0.0, "codes_erreur": {},
        "cpu_serveur_coeurs": 1.0, "rss_serveur_pic_mib": 50,
        "cpu_generateur_pct": 0.0, "mode": "P",
    }
    base.update(over)
    return base


def _sample_data():
    return {
        "environnement": {
            "cpu": "model name : Intel(R) Core(TM) i5-6300U CPU @ 2.40GHz",
            "nproc": 4, "commit": "c9d520e", "mode": "complet",
        },
        "scenarios": [
            _scenario(scenario="commande:help", rps=464.0, lat_moy_ms=13.8),
            _scenario(scenario="commande:get_messages", rps=83.1, lat_moy_ms=76.4),
            _scenario(scenario="commande:send_message", rps=141.8, lat_moy_ms=44.9,
                      p50_ms=12.0, p99_ms=578.4, lat_max_ms=1591.9),
            _scenario(scenario="commande:read_message", rps=259.8, lat_moy_ms=24.4,
                      p50_ms=7.6, p99_ms=251.0),
            _scenario(scenario="mixte:W8", rps=201.7),
            _scenario(scenario="regime_etabli:W8", rps=260.4),
            _scenario(scenario="sweep:W48:pass1", rps=65.6, erreurs=2,
                      taux_erreur_pct=0.17, codes_erreur={"ConnectionResetError": 2}),
            _scenario(scenario="mode:persistant", rps=172.8),
        ],
    }


def test_generate_writes_report_and_returns_text(tmp_path):
    src = tmp_path / "data.json"
    src.write_text(json.dumps(_sample_data()))
    out = tmp_path / "report.md"
    text = mr.generate(str(src), str(out))
    assert out.read_text() == text
    assert text.startswith("# Synapse — Pre-Optimization Performance Baseline")
    assert text.endswith("\n\n")  # last section line + trailing blank line


def test_all_five_sections_present(tmp_path):
    src = tmp_path / "data.json"
    src.write_text(json.dumps(_sample_data()))
    out = tmp_path / "report.md"
    text = mr.generate(str(src), str(out))
    for section in ("## A. Per-command cost", "## B. Realistic mixed load",
                    "## C. Steady state", "## D. Concurrency sweep",
                    "## E. Transport"):
        assert section in text
    # every scenario gets a data row
    for s in _sample_data()["scenarios"]:
        assert f"| {s['scenario']} |" in text


def test_error_codes_rendered(tmp_path):
    src = tmp_path / "data.json"
    src.write_text(json.dumps(_sample_data()))
    out = tmp_path / "report.md"
    text = mr.generate(str(src), str(out))
    assert "ConnectionResetError x2" in text
    assert "0.170%" in text


def test_findings_slowest_commands(tmp_path):
    src = tmp_path / "data.json"
    src.write_text(json.dumps(_sample_data()))
    out = tmp_path / "report.md"
    text = mr.generate(str(src), str(out))
    assert "`get_messages` (RPS 83.1, mean 76.4 ms)" in text
    assert "send_message`" in text  # tail finding exists


def test_real_baseline_json_renders_32_rows(tmp_path):
    """Guard the committed artifact: every scenario in the real JSON is in the report."""
    src = REPO / "benchmarks" / "results" / "baseline-pre-opt.json"
    out = tmp_path / "report.md"
    text = mr.generate(str(src), str(out))
    data = json.loads(src.read_text())
    for s in data["scenarios"]:
        assert f"| {s['scenario']} |" in text
    assert f"`{data['environnement']['commit']}`" in text
