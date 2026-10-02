import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from playlistbro.core.models import Track
from playlistbro.ui.player_widget import (
    PlayerWidget,
    _TransitionPlan,
    _plan_auto_mix,
    _transition_gains,
    _transition_playback_rate,
)


class _Calls:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


class PlayerWidgetTests(unittest.TestCase):
    def test_transition_uses_smooth_fade_and_tempo_curves(self):
        fade_start = _transition_gains(0.0)
        fade_middle = _transition_gains(0.5)
        fade_end = _transition_gains(1.0)

        self.assertAlmostEqual(fade_start[0], 1.0)
        self.assertAlmostEqual(fade_start[1], 0.0)
        self.assertAlmostEqual(sum(gain * gain for gain in fade_middle), 1.0)
        self.assertAlmostEqual(fade_end[0], 0.0)
        self.assertAlmostEqual(fade_end[1], 1.0)
        self.assertAlmostEqual(_transition_playback_rate(0.0, 1.2), 1.0)
        self.assertAlmostEqual(_transition_playback_rate(0.5, 1.2), 1.2)
        self.assertAlmostEqual(_transition_playback_rate(1.0, 1.2), 1.0)

    def test_transition_plan_aligns_beats_and_matches_local_energy(self):
        outgoing = Track(
            duration=100, tempo=120, beat_offset=0.25, loudness=-10,
            waveform_peaks=[0.8] * 2400,
        )
        incoming = Track(
            duration=120, tempo=100, beat_offset=0.1, loudness=-10,
            waveform_peaks=[0.1] * 800 + [0.8] * 1600,
        )

        plan = _plan_auto_mix(outgoing, incoming, 50000, 16000, energy_match=True)

        self.assertEqual(plan.outgoing_position_ms, 50250)
        self.assertEqual(plan.wait_ms, 250)
        self.assertAlmostEqual(plan.playback_rate, 1.2)
        self.assertGreater(plan.incoming_position_ms, 35000)
        self.assertLess(plan.incoming_position_ms, 55000)
        self.assertEqual(plan.duration_ms, 16000)
        phase = (plan.incoming_position_ms / 1000 - 0.1) / 0.6
        self.assertAlmostEqual(phase, round(phase))

    def test_energy_match_off_starts_at_each_track_grid(self):
        outgoing = Track(duration=100, tempo=120, beat_offset=0.25)
        incoming = Track(duration=120, tempo=100, beat_offset=0.1)

        plan = _plan_auto_mix(outgoing, incoming, 50000, 16000, energy_match=False)

        self.assertEqual(plan.incoming_position_ms, 100)
        self.assertEqual(plan.outgoing_position_ms, 50250)

    def test_bpm_grid_match_off_disables_alignment_and_tempo_lock(self):
        outgoing = Track(duration=100, tempo=120, beat_offset=0.25)
        incoming = Track(duration=120, tempo=100, beat_offset=0.1)

        plan = _plan_auto_mix(
            outgoing, incoming, 50000, 16000, energy_match=False, bpm_sync=False,
        )

        self.assertEqual(plan.outgoing_position_ms, 50000)
        self.assertEqual(plan.incoming_position_ms, 0)
        self.assertEqual(plan.wait_ms, 0)
        self.assertEqual(plan.playback_rate, 1.0)

    def test_energy_match_remains_available_without_bpm_grid_sync(self):
        outgoing = Track(
            duration=100, tempo=120, beat_offset=0.25, loudness=-10,
            waveform_peaks=[0.8] * 2400,
        )
        incoming = Track(
            duration=120, tempo=100, beat_offset=0.1, loudness=-10,
            waveform_peaks=[0.1] * 800 + [0.8] * 1600,
        )

        plan = _plan_auto_mix(
            outgoing, incoming, 50000, 16000, energy_match=True, bpm_sync=False,
        )

        self.assertEqual(plan.playback_rate, 1.0)
        self.assertEqual(plan.outgoing_position_ms, 50000)
        self.assertGreater(plan.incoming_position_ms, 35000)

    def test_transition_duration_is_capped_by_incoming_track_end(self):
        outgoing = Track(duration=100, tempo=120, beat_offset=0.25)
        incoming = Track(duration=10, tempo=100, beat_offset=0.1)

        plan = _plan_auto_mix(outgoing, incoming, 50000, 16000, energy_match=False)

        self.assertEqual(plan.duration_ms, 8250)

    def test_transition_completion_restores_normal_playback_rate(self):
        outgoing_player = SimpleNamespace(stop=_Calls())
        incoming_player = SimpleNamespace(
            position=lambda: 5000, duration=lambda: 120000,
        )
        outgoing_output = SimpleNamespace(setVolume=_Calls())
        incoming_output = SimpleNamespace(setVolume=_Calls())
        track = Track(filepath="incoming.mp3", title="Incoming")
        widget = SimpleNamespace(
            player=outgoing_player,
            _transition_player=incoming_player,
            audio_output=outgoing_output,
            _transition_output=incoming_output,
            volume_slider=SimpleNamespace(value=lambda: 80),
            _transition_elapsed=SimpleNamespace(elapsed=lambda: 16000, invalidate=_Calls()),
            _transition_plan=_TransitionPlan(0, 0, 0, 1.2, 16000),
            _transition_track=track,
            display_track=track,
            _transition_timer=SimpleNamespace(stop=_Calls()),
            _transition_pending_start=False,
            _transition_position_ready=True,
            _manual_seeking=False,
            _seek_guard_position=None,
            _crossfade_requested=True,
            _set_progress_blink=_Calls(),
            _set_display_track=_Calls(),
            _load_waveform=_Calls(),
            position_slider=SimpleNamespace(setRange=_Calls(), setValue=_Calls()),
            time_label=SimpleNamespace(setText=_Calls()),
            waveform_chart=SimpleNamespace(set_playhead=_Calls()),
            track_transitioned=SimpleNamespace(emit=_Calls()),
        )
        widget._sync_progress_from_player = lambda player: PlayerWidget._sync_progress_from_player(widget, player)

        PlayerWidget._advance_crossfade(widget)

        self.assertEqual(outgoing_player.stop.calls, [()])
        self.assertIs(widget.player, incoming_player)
        self.assertEqual(widget.position_slider.setRange.calls, [(0, 120000)])
        self.assertEqual(widget.position_slider.setValue.calls, [(5000,)])
        self.assertEqual(widget.waveform_chart.set_playhead.calls, [(5.0,)])
        self.assertIs(widget.current_track, track)

    def test_short_track_requests_auto_mix_when_playback_starts(self):
        emitted = _Calls()
        widget = SimpleNamespace(
            current_track=Track(duration=10, tempo=120, beat_offset=0.25),
            waveform_chart=SimpleNamespace(_precise_grid=None),
            transition_duration_ms=16000,
            _manual_seeking=False,
            _crossfade_requested=False,
            _is_playing=lambda: True,
            crossfade_btn=SimpleNamespace(isChecked=lambda: True),
            crossfade_requested=SimpleNamespace(emit=emitted),
        )

        self.assertTrue(PlayerWidget._maybe_request_crossfade(widget, 0, 10000))

        self.assertEqual(emitted.calls, [()])

    def test_manual_seek_rechecks_auto_mix_at_new_position(self):
        recheck = _Calls()
        widget = SimpleNamespace(
            player=SimpleNamespace(position=lambda: 99000, duration=lambda: 100000),
            _maybe_request_crossfade=recheck,
        )

        PlayerWidget._end_manual_seek(widget)

        self.assertFalse(widget._manual_seeking)
        self.assertEqual(recheck.calls, [(99000, 100000)])

    def test_incoming_track_metadata_updates_when_transition_starts(self):
        update_display = _Calls()
        incoming = Track(title="Incoming")
        widget = SimpleNamespace(
            _transition_track=incoming,
            display_track=None,
            _transition_player=SimpleNamespace(position=lambda: 5000),
            _set_display_track=update_display,
            _load_waveform=_Calls(),
            waveform_chart=SimpleNamespace(set_playhead=_Calls()),
        )

        PlayerWidget._show_transition_track(widget)

        self.assertEqual(update_display.calls, [(incoming,)])
        self.assertEqual(widget._load_waveform.calls, [(incoming,)])
        self.assertEqual(widget.waveform_chart.set_playhead.calls, [(5.0,)])

    def test_progress_tracks_outgoing_during_fade_while_waveform_tracks_incoming(self):
        incoming_player = SimpleNamespace()
        outgoing_player = SimpleNamespace(position=lambda: 99000, duration=lambda: 100000)
        track = Track(title="Incoming")
        playhead = _Calls()
        widget = SimpleNamespace(
            player=outgoing_player,
            _transition_player=incoming_player,
            _transition_track=track,
            display_track=track,
            waveform_chart=SimpleNamespace(set_playhead=playhead),
            position_slider=SimpleNamespace(setRange=_Calls(), setValue=_Calls()),
            time_label=SimpleNamespace(setText=_Calls()),
            is_crossfading=True,
            _seek_guard_position=None,
            _maybe_request_crossfade=_Calls(),
        )
        widget._sync_progress_from_player = (
            lambda player, update_waveform=True: PlayerWidget._sync_progress_from_player(
                widget, player, update_waveform,
            )
        )

        PlayerWidget._on_position_changed(widget, outgoing_player, 99000)
        PlayerWidget._on_position_changed(widget, incoming_player, 4300)

        self.assertEqual(playhead.calls, [(4.3,)])
        self.assertEqual(widget.position_slider.setValue.calls, [(99000,)])
        self.assertEqual(widget.time_label.setText.calls, [("1:39 / 1:40",)])

    def test_title_click_emits_current_track(self):
        emitted = _Calls()
        track = Track(filepath="song.mp3")
        widget = SimpleNamespace(
            current_track=track,
            display_track=None,
            track_title_clicked=SimpleNamespace(emit=emitted),
        )

        PlayerWidget._on_title_clicked(widget)

        self.assertEqual(emitted.calls, [(track,)])

    def test_waveform_seek_updates_progress_immediately(self):
        widget = SimpleNamespace(
            current_track=Track(filepath="song.mp3"),
            _cancel_crossfade=_Calls(),
            player=SimpleNamespace(duration=lambda: 120000, setPosition=_Calls()),
            position_slider=SimpleNamespace(setValue=_Calls()),
            time_label=SimpleNamespace(setText=_Calls()),
            waveform_chart=SimpleNamespace(set_playhead=_Calls()),
            _maybe_request_crossfade=_Calls(),
        )

        PlayerWidget._seek_to(widget, 42.5)

        self.assertEqual(widget.position_slider.setValue.calls, [(42500,)])
        self.assertEqual(widget.time_label.setText.calls, [("0:42 / 2:00",)])
        self.assertEqual(widget.waveform_chart.set_playhead.calls, [(42.5,)])
        self.assertEqual(widget.player.setPosition.calls, [(42500,)])


if __name__ == "__main__":
    unittest.main()