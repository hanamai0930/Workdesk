"""Own-window selection and visible-frame sizing for the desktop launcher."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('desktop_window', Path(__file__).resolve().parents[1]/'workdesk-ui/desktop_window.py')
desktop = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(desktop)


class ImmediateThread:
    def __init__(self, *, target, name, daemon):
        assert not daemon  # A short-lived --open process must finish sizing.
        self.target = target

    def start(self):
        self.target()


class DesktopWindowTest(unittest.TestCase):
    def launch(self, candidates, *, timeout=False):
        native = Mock()
        native.handles.side_effect = [{10}, {10, 20, 30}]
        native.is_workdesk.side_effect = lambda h, _: h in candidates
        native.size.return_value = {'verified': True, 'width': 1440, 'height': 900}
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)/'launch.json'
            with patch.object(desktop, 'NativeWindows', return_value=native), patch.object(desktop.threading, 'Thread', ImmediateThread):
                with patch.object(desktop.time, 'monotonic', side_effect=[0, 13] if timeout else [0, 0]):
                    spawn = Mock(return_value='process')
                    self.assertEqual(desktop.launch_sized('edge.exe', ['edge.exe'], spawn, report), 'process')
                spawn.assert_called_once_with(['edge.exe'])
            return native, json.loads(report.read_text(encoding='utf-8'))

    def test_only_one_new_workdesk_window_is_resized(self):
        native, report = self.launch({20})
        native.size.assert_called_once_with(20)
        self.assertTrue(report['verified'])
        self.assertEqual(report['window'], 20)
        self.assertNotIn(10, [c.args[0] for c in native.is_workdesk.call_args_list])

    def test_ambiguous_new_windows_are_left_alone(self):
        native, report = self.launch({20, 30})
        native.size.assert_not_called()
        self.assertFalse(report['verified'])
        self.assertIn('Ambiguous', report['error'])

    def test_no_window_timeout_does_not_resize_old_windows(self):
        native, report = self.launch(set(), timeout=True)
        native.size.assert_not_called()
        self.assertFalse(report['verified'])
        self.assertIn('timeout', report['error'])

    def test_visible_frame_excludes_invisible_resize_borders(self):
        native = object.__new__(desktop.NativeWindows)
        native.user = Mock()
        native.user.IsZoomed.return_value = native.user.IsIconic.return_value = False
        native.physical_pixels = nullcontext
        native.bounds = Mock(side_effect=[(17,10,948,1015), (9,10,956,1023), (240,70,1680,970)])
        native.work_area = lambda _: (0,0,1920,1040)
        report = native.size(20)
        native.user.SetWindowPos.assert_called_once_with(20, None, 232, 70, 1456, 908, 0x14)
        self.assertTrue(report['verified'])
        self.assertEqual([report['width'],report['height']], [1440,900])

    def test_small_screen_limits_the_requested_visible_size(self):
        native = object.__new__(desktop.NativeWindows)
        native.user = Mock()
        native.user.IsZoomed.return_value = native.user.IsIconic.return_value = False
        native.physical_pixels = nullcontext
        native.bounds = Mock(side_effect=[(10,10,1010,710), (2,10,1018,718), (0,0,1280,720)])
        native.work_area = lambda _: (0,0,1280,720)
        report = native.size(20)
        self.assertEqual(report['target'], [1280,720])
        self.assertTrue(report['verified'])


if __name__ == '__main__':
    unittest.main()
