import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import m2_index as idx
import m2_feedback as feedback
import m2_policy as policy


class IndexTests(unittest.TestCase):
    def test_selected_ranges_merge_handles_and_ignore_rejects(self):
        plan = {"candidates": [
            {"status": "shortlisted", "source_in": 10, "source_out": 20},
            {"status": "approved_for_timing", "source_in": 35, "source_out": 40},
            {"status": "reject", "source_in": 90, "source_out": 99}]}
        self.assertEqual(idx.selected_intervals(plan, 100, 10), [[0, 50]])

    def test_empty_or_unapproved_shortlist_fails(self):
        with self.assertRaises(ValueError):
            idx.selected_intervals({"candidates": [{"source_in": 1, "source_out": 2}]}, 100)

    def test_handles_clamp_and_nonfinite_rejected(self):
        self.assertEqual(idx.selected_intervals({"candidates": [{"status": "shortlisted", "source_in": 90, "source_out": 99}]}, 100), [[60, 100]])
        for invalid in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError):
                idx.selected_intervals({"candidates": []}, 100, invalid)

    def test_decoder_context_overlaps_but_cores_do_not(self):
        units = idx.timing_units([[0, 250]])
        self.assertEqual([(u['core_start'],u['core_end']) for u in units], [(0,120),(120,240),(240,250)])
        self.assertEqual(units[0]['end'],121)
        self.assertEqual(units[1]['start'],119)

    def test_suspect_speech_is_retained(self):
        segments = [{"start": n*30, "end": (n+1)*30, "text": "Yes yes"} for n in range(3)]
        report = idx.coverage_report(segments,90)
        self.assertEqual(report['cue_count'],3)
        self.assertEqual(len(report['suspected_asr_artifacts']),3)
        self.assertEqual(report['status'],'review_coverage')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'transcript.vtt'
            idx.write_vtt(segments,p)
            self.assertEqual(len(idx.read_vtt(p)),3)

    def test_missing_audio_text_is_unexplained_not_silence(self):
        report=idx.coverage_report([{'start':60,'end':70,'text':'Hello'}],100)
        self.assertEqual(report['unexplained_gaps'][0],{'start':0.0,'end':60.0,'status':'unexplained'})

    def test_invalid_cues_fail_before_analysis(self):
        for segment in [{'start':0,'end':0,'text':'x'}, {'start':0,'end':float('nan'),'text':'x'}]:
            with self.assertRaises(ValueError):
                idx.coverage_report([segment],100)

    def test_discovery_never_runs_word_decoder(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'source.wav';audio.write_bytes(b'audio')
            out=root/'index'
            def fake_run(cmd,**kwargs):
                calls.append(cmd)
                if cmd[0]==idx.WHISPER:
                    output=Path(cmd[cmd.index('--output-dir')+1]); output.mkdir(parents=True,exist_ok=True)
                    (output/'result.json').write_text(json.dumps({'segments':[{'start':0,'end':5,'text':'Hello there'}]}))
            with patch.object(idx,'duration_seconds',return_value=5),patch.object(idx,'run',side_effect=fake_run):
                words,vtt=idx.transcribe(str(audio),str(out),3600)
                self.assertIsNone(words)
                decoder=[c for c in calls if c[0]==idx.WHISPER]
                self.assertEqual(len(decoder),1)
                self.assertEqual(decoder[0][decoder[0].index('--word-timestamps')+1],'False')
                self.assertFalse((out/'words.json').exists())
                idx.transcribe(str(audio),str(out),3600)
                self.assertEqual(len([c for c in calls if c[0]==idx.WHISPER]),1)

    def test_legacy_or_changed_transcript_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'source.wav';audio.write_bytes(b'audio')
            out=root/'index';out.mkdir();(out/'transcript.vtt').write_text('WEBVTT\n')
            with patch.object(idx,'duration_seconds',return_value=5),self.assertRaises(ValueError):
                idx.transcribe(str(audio),str(out),3600)

    def test_word_timing_is_only_selected_source_span(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'source.wav';audio.write_bytes(b'audio');out=root/'index';out.mkdir()
            plan=root/'plan.json';plan.write_text(json.dumps({'candidates':[{'status':'shortlisted','source_in':200,'source_out':210}]}))
            calls=[]
            def fake_run(cmd,**kwargs):
                calls.append(cmd)
                if cmd[0]==idx.WHISPER:
                    output=Path(cmd[cmd.index('--output-dir')+1]);output.mkdir(parents=True,exist_ok=True)
                    (output/'result.json').write_text(json.dumps({'segments':[{'words':[{'word':' Hi','start':1,'end':2}]}]}))
            with patch.object(idx,'duration_seconds',return_value=3600),patch.object(idx,'run',side_effect=fake_run):
                result=idx.time_selected(str(audio),str(out),str(plan),handles=5)
                data=json.loads(Path(result).read_text())
                self.assertEqual(data['input']['intervals'],[[195,215]])
                self.assertEqual(data['segments'][0]['words'][0]['start'],196)
                self.assertEqual(len([c for c in calls if c[0]==idx.WHISPER]),1)

    def test_explicit_nonverbal_shortlist_does_not_invent_word_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'source.wav';audio.write_bytes(b'audio');out=root/'index';out.mkdir()
            plan=root/'plan.json';plan.write_text(json.dumps({'candidates':[{'slug':'action','status':'shortlisted','source_in':1,'source_out':4,'non_verbal':True}]}))
            with patch.object(idx,'duration_seconds',return_value=5),patch.object(idx,'run') as run:
                path=idx.time_selected(str(audio),str(out),str(plan))
                self.assertEqual(json.loads(Path(path).read_text())['status'],'not_applicable_explicit_nonverbal')
                run.assert_not_called()

    def test_profile_duration_is_the_enforced_default(self):
        self.assertEqual(policy.duration_policy()['hard_ceiling_seconds'],35)
        override=policy.duration_policy({'duration_policy':{'hard_ceiling_seconds':40}})
        self.assertEqual(override['hard_ceiling_seconds'],40)
        self.assertTrue(override['explicit_edl_override'])
        with self.assertRaises(ValueError):
            policy.duration_policy({'duration_policy':{'minimum_seconds':10}})

    def test_partial_vision_index_resumes_missing_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);sheets=out/'vision';sheets.mkdir();(sheets/'sheet_000000.jpg').write_bytes(b'existing')
            def fake_run(command,**kwargs):
                Path(command[-2]).write_bytes(b'new')
            with patch.object(idx.subprocess,'run',side_effect=fake_run) as run:
                idx.vision('proxy.mp4',str(out),600)
                self.assertEqual(run.call_count,1)
                self.assertTrue((sheets/'sheet_000300.jpg').exists())


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.profile=json.loads(feedback.DEFAULT_PROFILE.read_text())
        self.record={
            'post_id':'123','platform':'test','source_type':'reaction','audience_profile':self.profile['id'],
            'candidate_slug':'clip','revision_reason':'initial','published_at':'2026-09-06T12:00:00Z',
            'observed_at':'2026-09-07T12:00:00Z','reporting_interval_hours':24,
            'published_render_sha256':'a'*64,'duration_seconds':25,
            'predictions':{'recorded_at':'2026-09-06T11:00:00Z','stop':3,'hold':3,'send':2,'audience_fit':2},
            'metrics':{'views':100,'shares':None,'saves':0,'comments':2,'average_watch_seconds':None,'completion_rate':None}}

    def test_missing_metrics_remain_missing(self):
        out=feedback.summarize([self.record],self.profile)['cohorts'][0]
        self.assertIsNone(out['shares_per_view'])
        self.assertEqual(out['saves_per_view'],0)
        self.assertEqual(out['comments_per_view'],.02)
        self.assertIsNone(out['median_watch_fraction'])

    def test_retrospective_predictions_rejected(self):
        self.record['predictions']['recorded_at']='2026-09-07T00:00:00Z'
        with self.assertRaises(ValueError):feedback.validate(self.record,self.profile)

    def test_post_intervals_do_not_mix(self):
        other=json.loads(json.dumps(self.record));other.update(reporting_interval_hours=168, observed_at='2026-09-13T12:00:00Z')
        self.assertEqual(len(feedback.summarize([self.record,other],self.profile)['cohorts']),2)

    def test_send_requires_concrete_record(self):
        self.record['predictions']['send']=3
        with self.assertRaises(ValueError):feedback.validate(self.record,self.profile)

    def test_duplicate_observation_is_idempotent_and_conflict_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'posts.jsonl'
            self.assertEqual(feedback.append_observation(path,self.record),'recorded')
            self.assertEqual(feedback.append_observation(path,self.record),'already_recorded')
            self.record['metrics']['views']=200
            with self.assertRaises(ValueError):feedback.append_observation(path,self.record)
            self.assertEqual(json.loads(path.read_text())['metrics']['views'],100)

    def test_zero_views_and_small_sample_do_not_produce_claim(self):
        self.record['metrics']['views']=0
        report=feedback.summarize([self.record],self.profile)
        self.assertFalse(report['preferences_changed'])
        self.assertFalse(report['cohorts'][0]['preference_review_due'])
        self.assertIsNone(report['cohorts'][0]['comments_per_view'])


if __name__=='__main__':unittest.main()
