#!/usr/bin/env python3
"""Focused regression tests for the shared Method 2 quality contract."""

from __future__ import annotations

import json
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import m2_quality  # noqa: E402
import m2_review  # noqa: E402


class QualityEvidenceTests(unittest.TestCase):
    def test_freezedetect_unmatched_eof_start_fails(self) -> None:
        closed = m2_quality.parse_freezedetect(
            "freeze_start: 1.0\nfreeze_duration: 0.8\nfreeze_end: 1.8", 3.0
        )
        self.assertTrue(closed["tail_ok"])

        eof = m2_quality.parse_freezedetect(
            "freeze_start: 2.0", 3.0
        )
        self.assertFalse(eof["tail_ok"])
        self.assertEqual(eof["tail_failures"][0]["reason"], "unmatched_freeze_start")

    def test_audio_content_is_not_semantic_proof_and_nonverbal_is_na(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vtt = Path(directory) / "rendered.vtt"
            vtt.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nThe red bicycle lands safely.\n")
            speech = m2_quality.audio_content_gate(
                {"beats": [{"id": "b1", "text": "The red bicycle lands safely."}]}, vtt
            )
            self.assertEqual(speech["status"], "checked")
            self.assertTrue(speech["passed"])
            self.assertFalse(speech["semantic_proof"])
            nonverbal = m2_quality.audio_content_gate(
                {"beats": [{"id": "b1", "text": "", "non_verbal": True}]}, vtt
            )
            self.assertEqual(nonverbal["status"], "not_applicable")
            self.assertIsNone(nonverbal["passed"])
            self.assertTrue(nonverbal["required_visual_review"])

    def test_alignment_binds_candidate_offsets_and_asset_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "range.mp4"
            audio = root / "range.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            edl = {
                "slug": "example",
                "source_in_seconds": 100.0,
                "source_out_seconds": 110.0,
                "handled_start": 70.0,
                "handled_end": 140.0,
                "source_video": str(video),
                "source_audio": str(audio),
                "alignment_report": "alignment.json",
                "source_identity": {"id": "synthetic-source"},
            }
            edl_path = root / "edl.json"
            edl_path.write_text(json.dumps(edl))
            report = {
                "schema": "method-2.range-acquisition.v3",
                "status": "REVIEW_REQUIRED",
                "source_fingerprint": {"id": "synthetic-source"},
                "candidates": {
                    "example": {
                        "slug": "example",
                        "status": "PASS",
                        "source_in": 100.0,
                        "source_out": 110.0,
                        "handled_start": 70.0,
                        "handled_end": 140.0,
                        "asset_fingerprints": {
                            "video": m2_quality.file_fingerprint(video),
                            "audio": m2_quality.file_fingerprint(audio),
                        },
                    }
                },
            }
            (root / "alignment.json").write_text(json.dumps(report))
            self.assertTrue(m2_quality.alignment_gate(edl, edl_path)["passed"])
            report["candidates"]["example"]["source_out"] = 111.0
            (root / "alignment.json").write_text(json.dumps(report))
            self.assertFalse(m2_quality.alignment_gate(edl, edl_path)["passed"])
            report["candidates"]["example"]["source_out"] = 110.0
            report["candidates"]["example"]["asset_fingerprints"]["video"]["sha256"] = "stale"
            (root / "alignment.json").write_text(json.dumps(report))
            self.assertFalse(m2_quality.alignment_gate(edl, edl_path)["passed"])

    def test_review_missing_inspected_hash_bound_evidence_stays_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render = root / "render.mp4"
            edl_path = root / "edl.json"
            vtt = root / "rendered.vtt"
            render.write_bytes(b"render")
            vtt.write_text("WEBVTT\n")
            edl = {"slug": "one", "beats": [{"id": "b1", "in": 0, "out": 1}]}
            edl_path.write_text(json.dumps(edl))
            record = {
                "render_sha256": m2_quality.sha256_file(render),
                "edl_sha256": m2_quality.sha256_file(edl_path),
                "visual_review": {
                    "status": "PASS", "reviewed": True, "reviewer": "clip_executor",
                    "checks": {}, "source_metadata": {}, "evidence": [], "notes": "looked",
                },
                "semantic_review": {"status": "PASS", "reviewed": True, "reviewer": "clip_planner", "checks": {}},
            }
            result = m2_quality.validate_review_record(record, render, edl_path, vtt, edl=edl)
            self.assertFalse(result["passed"])
            self.assertIn("review evidence", [item["name"] for item in result["checks"]])

    def test_fresh_transcription_cannot_relabel_old_vtt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); clip=root/'render.mp4'; clip.write_bytes(b'new render')
            vtt=root/'rendered.vtt'; vtt.write_text('WEBVTT\n\nOld words\n')
            provenance=Path(str(vtt)+'.provenance.json')
            provenance.write_text(json.dumps({'render_sha256':'old','vtt':str(vtt)}))
            with patch.object(m2_quality.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
                with self.assertRaises(RuntimeError):
                    m2_quality.transcribe_rendered_vtt(clip,vtt)
            self.assertEqual(json.loads(provenance.read_text())['render_sha256'],'old')
            self.assertIn('Old words',vtt.read_text())

    def test_transcription_reuse_requires_matching_bytes_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);clip=root/'render.mp4';clip.write_bytes(b'render');vtt=root/'rendered.vtt'
            def decoder(command,**kwargs):
                out=Path(command[command.index('--output-dir')+1]);(out/'rendered.vtt').write_text('WEBVTT\n\n00:00.000 --> 00:01.000\nFresh words\n')
                return subprocess.CompletedProcess(command,0,'','')
            with patch.object(m2_quality.subprocess,'run',side_effect=decoder) as run:
                m2_quality.transcribe_rendered_vtt(clip,vtt)
                m2_quality.transcribe_rendered_vtt(clip,vtt)
                self.assertEqual(run.call_count,1)
                vtt.write_text('WEBVTT\n\nChanged words\n')
                m2_quality.transcribe_rendered_vtt(clip,vtt)
                self.assertEqual(run.call_count,2)
                m2_quality.transcribe_rendered_vtt(clip,vtt,model='different-test-model')
                self.assertEqual(run.call_count,3)

    def test_complete_review_requires_each_splice_and_current_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);clip=root/'render.mp4';clip.write_bytes(b'render')
            source=root/'source.vtt';source.write_text('WEBVTT\n\n00:00.000 --> 00:03.000\nFixture source words\n')
            vtt=root/'rendered.vtt';m2_quality.write_nonverbal_vtt(clip,vtt)
            edl={'slug':'fixture','source_vtt':str(source),'beats':[{'in':0,'out':1},{'in':1,'out':2},{'in':2,'out':3}]}
            plan=root/'edl.json';plan.write_text(json.dumps(edl))
            evidence=[]
            kinds=[('opening_still',None),('end_still',None),('payoff_moving_excerpt',None)]
            for index in (1,2):
                kinds.extend([(kind,index) for kind in ('splice_before_still','splice_after_still','splice_moving_excerpt')])
            for kind,index in kinds:
                path=root/f'{kind}-{index}.bin';path.write_bytes(f'{kind}-{index}'.encode())
                item={'type':kind,'path':str(path)}
                if index is not None:item['splice_index']=index
                evidence.append(item)
            metadata={'source_resolution':{'width':640,'height':360},'crop_scale':[5.33],'crop_magnification':[5.33]}
            with patch.object(m2_quality,'source_visual_metadata',return_value=metadata):
                record=m2_review.make_template(plan,clip,vtt,evidence,edl)
            for section,actor in [('visual_review','clip_executor'),('semantic_review','clip_planner')]:
                obj=record[section];obj.update(status='PASS',reviewed=True,reviewer=actor,notes='Simulated complete review for a unit-test fixture.')
                for check in obj['checks'].values():check.update(completed=True,reviewed=True,status='PASS',notes='Fixture assertion.')
            for item in record['visual_review']['evidence']:item.update(reviewed=True,inspected=True)
            self.assertTrue(m2_quality.validate_review_record(record,clip,plan,vtt,edl=edl)['passed'])
            mutations=[
                lambda r:r['semantic_review']['rendered_vtt'].pop('sha256'),
                lambda r:r['semantic_review']['checks'].pop('material_qualifiers'),
                lambda r:r['semantic_review'].update(reviewer='clip_executor'),
                lambda r:r['visual_review']['evidence'][-1].update(sha256='stale'),
                lambda r:r['visual_review']['evidence'].pop(),
                lambda r:r['visual_review']['evidence'][0].update(inspected=False),
                lambda r:r['visual_review']['checks'].update(cadence=True),
            ]
            for mutate in mutations:
                changed=copy.deepcopy(record);mutate(changed)
                self.assertFalse(m2_quality.validate_review_record(changed,clip,plan,vtt,edl=edl)['passed'])
            vtt.write_text('WEBVTT\n\nChanged transcript\n')
            self.assertFalse(m2_quality.validate_review_record(record,clip,plan,vtt,edl=edl)['passed'])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_presentation_cadence_uses_frame_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cadence.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc=size=160x90:rate=30", "-t", "0.5", "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output), "-y",
            ], check=True)
            result = m2_quality.presentation_cadence(output, "30/1")
            self.assertTrue(result["passed"])
            self.assertGreater(result["frame_count"], 1)


if __name__ == "__main__":
    unittest.main()
