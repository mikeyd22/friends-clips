from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import m2_alignment  # noqa: E402
import m2_ranges  # noqa: E402


class Method2AlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(42)
        self.full = rng.normal(0, 1, 7000).astype(np.float32)
        self.handled_start = 10.0
        self.good_native = self.full[1000:4000]

    def test_native_video_audio_catches_misaligned_video_even_when_replacement_audio_is_correct(self) -> None:
        # The assembly WAV is the correct planned slice, but the actual video
        # carries audio from one second later.  The replacement WAV must not be
        # allowed to make this candidate pass.
        misaligned_native = self.full[1100:4100]
        result = m2_alignment.verify_audio_arrays(
            self.full, misaligned_native, self.handled_start, duration_seconds=30
        )
        replacement_result = m2_alignment.verify_audio_arrays(
            self.full, self.good_native, self.handled_start, duration_seconds=30
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("offset", result["reason"])
        self.assertEqual(replacement_result["status"], "PASS")

    def test_process_fails_when_replacement_audio_is_swapped_even_if_video_proof_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            ranges = source_dir / "ranges"
            ranges.mkdir(parents=True)
            full_audio_path = source_dir / "full.m4a"
            proxy_path = source_dir / "proxy.mp4"
            full_audio_path.write_bytes(b"full-source")
            proxy_path.write_bytes(b"proxy")
            video_path = ranges / "candidate.mp4"
            audio_path = ranges / "candidate-audio.wav"
            video_path.write_bytes(b"video")
            audio_path.write_bytes(b"audio")
            (root / "m2.json").write_text(
                json.dumps(
                    {
                        "source": {
                            "mode": "file",
                            "id": "fixture",
                            "duration_seconds": 100,
                            "full_audio": str(full_audio_path),
                            "proxy": str(proxy_path),
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "plan.json").write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "id": "C1",
                                "slug": "candidate",
                                "status": "approved_for_execution",
                                "source_in": 10,
                                "source_out": 40,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            shifted = self.full[1100:4100]
            with patch.object(m2_ranges, "verify_proxy_alignment", return_value={"status": "PASS"}), patch.object(
                m2_ranges,
                "verify_candidate_video",
                return_value={"status": "PASS", "method": "synthetic_video_proof", "reason": "video pass"},
            ), patch.object(m2_ranges, "env_of", side_effect=[self.full, shifted]):
                report, _, state = m2_ranges.process_project(root, handles=0, validate_existing=True)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(state["stage"], "ranges_failed")
            candidate_report = report["candidates"][0]
            self.assertEqual(candidate_report["status"], "FAIL")
            self.assertEqual(
                candidate_report["verification"]["assembly_audio_verification"]["status"],
                "FAIL",
            )

    def test_low_confidence_is_pending_without_explicit_transcript_record(self) -> None:
        result = m2_alignment.verify_audio_arrays(
            np.zeros(7000, dtype=np.float32),
            np.zeros(3000, dtype=np.float32),
            self.handled_start,
            duration_seconds=30,
        )
        self.assertEqual(result["status"], "PENDING_REVIEW")
        self.assertNotEqual(result["status"], "PASS")

    def test_low_confidence_proxy_audio_requires_hash_bound_review_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proxy = root / "proxy.mp4"
            full_audio = root / "full.m4a"
            evidence = root / "proxy-review.json"
            proxy.write_bytes(b"proxy-current")
            full_audio.write_bytes(b"audio-current")
            common = {
                "status": "PASS",
                "method": "explicit transcript verification of proxy audio",
                "independent": True,
                "reviewer": "planner-review",
                "observed_anchor_notes": "Three VTT anchors checked against source audio.",
                "observed_anchors": [
                    {"source_seconds": 0, "media_seconds": 0, "text": "a"},
                    {"source_seconds": 10, "media_seconds": 10, "text": "b"},
                    {"source_seconds": 20, "media_seconds": 20, "text": "c"},
                ],
                "source_start_seconds": 0,
            }
            state = {"source": {"proxy": str(proxy), "full_audio": str(full_audio), "proxy_alignment": common}}
            with patch.object(m2_alignment, "has_stream", return_value=True):
                pending = m2_alignment.verify_proxy_alignment(
                    state,
                    full_audio_env=np.zeros(7000, dtype=np.float32),
                    proxy_audio_env=np.zeros(3000, dtype=np.float32),
                )
            self.assertEqual(pending["status"], "PENDING_REVIEW")
            common.update(
                {
                    "evidence_path": str(evidence),
                    "proxy_sha256": hashlib.sha256(proxy.read_bytes()).hexdigest(),
                    "source_audio_sha256": hashlib.sha256(full_audio.read_bytes()).hexdigest(),
                }
            )
            evidence.write_text('{"anchors":[1,2,3]}', encoding="utf-8")
            common["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            with patch.object(m2_alignment, "has_stream", return_value=True):
                resolved = m2_alignment.verify_proxy_alignment(
                    state,
                    full_audio_env=np.zeros(7000, dtype=np.float32),
                    proxy_audio_env=np.zeros(3000, dtype=np.float32),
                )
            self.assertEqual(resolved["status"], "PASS")
            self.assertEqual(resolved["resolution"], "explicit_transcript_verification")

    def test_explicit_transcript_record_can_resolve_low_confidence_when_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "range.mp4"
            full_audio = root / "full.m4a"
            evidence = root / "transcript-review.json"
            video.write_bytes(b"video-current")
            full_audio.write_bytes(b"full-audio-current")
            evidence.write_text('{"anchors":[{"source":10,"text":"opening"}]}', encoding="utf-8")
            candidate = {
                "slug": "candidate",
                "source_in": "00:00:12.000",
                "source_out": "00:00:42.000",
                "handled_start": 0.0,
                "handled_end": 72.0,
                "video_file": str(video),
                "alignment_transcript_verification": {
                    "status": "PASS",
                    "method": "explicit transcript verification of native audio",
                    "evidence_path": str(evidence),
                    "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                    "source_audio_sha256": hashlib.sha256(full_audio.read_bytes()).hexdigest(),
                    "source_in": "00:00:12.000",
                    "source_out": "00:00:42.000",
                    "handled_start": 0.0,
                    "handled_end": 72.0,
                    "reviewer": "planner-review",
                    "observed_anchor_notes": "Opening, middle, and closing words were checked against VTT.",
                    "observed_anchors": [
                        {"source_seconds": 12.0, "media_seconds": 12.0, "text": "opening"},
                        {"source_seconds": 27.0, "media_seconds": 27.0, "text": "middle"},
                        {"source_seconds": 42.0, "media_seconds": 42.0, "text": "closing"},
                    ],
                },
            }
            state = {"source": {"full_audio": str(full_audio)}}
            report = m2_alignment.verify_candidate_video(
                candidate,
                state,
                full_audio_env=np.zeros(7000, dtype=np.float32),
                native_audio_present=True,
                native_audio_env=np.zeros(3000, dtype=np.float32),
            )
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["resolution"], "explicit_transcript_verification")

    def test_drift_is_failed_when_early_and_middle_anchors_show_different_offsets(self) -> None:
        # Each section is a recognizable source slice, but the mapping changes
        # during the file.  This is the failure that a single head correlation
        # misses.
        probe = np.concatenate(
            [self.full[1000:1800], self.full[2000:2800], self.full[3100:3900]]
        )
        result = m2_alignment.verify_audio_arrays(
            self.full, probe, self.handled_start, duration_seconds=24
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("drift", result["reason"])
        self.assertGreater(result["offset_span_seconds"], 0.5)

    def test_visual_anchors_reject_a_shifted_video(self) -> None:
        rng = np.random.default_rng(7)
        frames = {i: rng.normal(size=(36, 64)).astype(np.float32) for i in range(120)}

        def proxy(seconds: float) -> np.ndarray:
            return frames[round(seconds)]

        def aligned_range(seconds: float) -> np.ndarray:
            return frames[round(seconds + 20)]

        def shifted_range(seconds: float) -> np.ndarray:
            return frames[round(seconds + 21)]
        self.assertEqual(
            m2_alignment.verify_visual_frames(proxy, aligned_range, 20, 40)["status"],
            "PASS",
        )
        shifted = m2_alignment.verify_visual_frames(proxy, shifted_range, 20, 40)
        self.assertEqual(shifted["status"], "FAIL")
        self.assertIn("offset", shifted["reason"])

    def test_handle_clamping_preserves_original_boundaries(self) -> None:
        bounds, issue = m2_ranges._candidate_bounds(
            {"source_in": "00:00:05.000", "source_out": "00:00:20.000"},
            30,
            100,
        )
        self.assertIsNone(issue)
        self.assertEqual(bounds["handled_start"], 0.0)
        self.assertEqual(bounds["handled_end"], 50.0)
        self.assertEqual(bounds["actual_handle_before_seconds"], 5.0)
        end_bounds, end_issue = m2_ranges._candidate_bounds(
            {"source_in": "00:01:25.000", "source_out": "00:01:40.000"},
            30,
            100,
        )
        self.assertIsNone(end_issue)
        self.assertEqual(end_bounds["handled_start"], 55.0)
        self.assertEqual(end_bounds["handled_end"], 100.0)
        self.assertEqual(end_bounds["actual_handle_after_seconds"], 0.0)

    def test_empty_plan_fails_and_records_accurate_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m2.json").write_text(json.dumps({"source": {}}), encoding="utf-8")
            (root / "plan.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
            report, _, state = m2_ranges.process_project(root)
            self.assertEqual(report["status"], "EMPTY")
            self.assertEqual(state["stage"], "ranges_failed")
            self.assertEqual(json.loads((root / "qa" / "range-acquisition.json").read_text())["status"], "EMPTY")

    def test_existing_range_without_matching_metadata_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            ranges = source / "ranges"
            ranges.mkdir(parents=True)
            full_audio = source / "full.m4a"
            full_audio.write_bytes(b"full")
            video = ranges / "approved.mp4"
            audio = ranges / "approved-audio.wav"
            video.write_bytes(b"old-video")
            audio.write_bytes(b"old-audio")
            state = {
                "source": {
                    "mode": "file",
                    "duration_seconds": 100,
                    "full_audio": str(full_audio),
                    "master": str(root / "missing-master.mp4"),
                }
            }
            candidate = {"id": "C1", "slug": "approved", "source_in": 10, "source_out": 20}
            m2_ranges.fetch(state, candidate, ranges, 30, project=root)
            self.assertEqual(candidate["_range_issue_code"], "STALE_OR_UNVERIFIED_EXISTING_RANGE")
            self.assertEqual(video.read_bytes(), b"old-video")
            self.assertEqual(audio.read_bytes(), b"old-audio")


if __name__ == "__main__":
    unittest.main()
