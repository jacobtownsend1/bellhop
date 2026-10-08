import curses
import unittest
from unittest.mock import Mock, patch

from bellhop.tmux import Session, TmuxError
from bellhop.ui import Lobby, sanitize_label, clip_label


class Screen:
    def __init__(self, keys, size=(20, 80)):
        self.keys = iter(keys)
        self.size = size
        self.drawn = []
        self.writes = []
        self.cursor = None

    def getmaxyx(self):
        return self.size

    def get_wch(self):
        return next(self.keys)

    def addstr(self, y, x, text, *args):
        self.drawn.append(text)
        self.writes.append((y, x, text, args[0] if args else 0))

    def move(self, y, x):
        height, width = self.size
        if not (0 <= y < height and 0 <= x < width):
            raise AssertionError('Cursor outside terminal')
        self.cursor = (y, x)

    def erase(self): pass
    def refresh(self): pass
    def timeout(self, value): pass
    def keypad(self, value): pass


class LobbyTests(unittest.TestCase):
    def adapter(self, sessions=None):
        tmux = Mock()
        tmux.list_sessions.return_value = sessions or []
        tmux.default_name.return_value = 'bellhop-1'
        return tmux

    def run_ui(self, tmux, keys, size=(20, 80)):
        screen = Screen(keys, size)
        with patch('bellhop.ui.curses.curs_set'):
            target = Lobby(screen, tmux).run()
        return target, screen

    def test_empty_lobby_never_creates(self):
        tmux = self.adapter()
        target, screen = self.run_ui(tmux, ['\x1b'])
        self.assertIsNone(target)
        tmux.create_session.assert_not_called()
        self.assertIn('No sessions yet', ' '.join(screen.drawn))

    def test_navigation_keys(self):
        sessions = [Session('$' + str(i), str(i), 1, 0) for i in range(3)]
        for keys, expected in [(['j', '\n'], '$1'), (['j', 'k', '\n'], '$0'),
                               (['l', '\n'], '$2'), (['l', 'h', '\n'], '$0'),
                               ([curses.KEY_DOWN, '\n'], '$1'),
                               ([curses.KEY_DOWN, curses.KEY_UP, '\n'], '$0')]:
            with self.subTest(keys=keys):
                self.assertEqual(self.run_ui(self.adapter(sessions), keys)[0], expected)

    def test_name_prompt_letters(self):
        tmux = self.adapter()
        tmux.create_session.return_value = Session('$5', 'ndhjkl', 1, 0)
        target, _ = self.run_ui(tmux, ['n', '\x15', *'ndhjkl', '\n'])
        self.assertEqual(target, '$5')
        self.assertEqual(tmux.create_session.call_args.args[0], 'ndhjkl')

    def test_create_cancel(self):
        tmux = self.adapter()
        self.run_ui(tmux, ['n', '\x1b', '\x1b'])
        tmux.create_session.assert_not_called()

    def test_delete_cancel(self):
        tmux = self.adapter([Session('$1', 'a', 1, 0)])
        self.run_ui(tmux, ['d', '\x1b', '\x1b'])
        tmux.kill_session.assert_not_called()

    def test_delete_confirmation(self):
        tmux = self.adapter([Session('$1', 'a', 1, 0)])
        self.run_ui(tmux, [curses.KEY_DC, 'y', '\x1b'])
        tmux.kill_session.assert_called_once_with(Session('$1', 'a', 1, 0))

    def test_refresh_preserves_selection(self):
        tmux = self.adapter()
        a, b = Session('$1', 'a', 1, 0), Session('$2', 'b', 1, 0)
        tmux.list_sessions.side_effect = [[a, b], [b, a]]
        self.assertEqual(self.run_ui(tmux, ['j', 'r', '\n'])[0], '$2')

    def test_disappeared_session_error(self):
        tmux = self.adapter([Session('$1', 'a', 1, 0)])
        tmux.kill_session.side_effect = TmuxError('gone')
        _, screen = self.run_ui(tmux, ['d', 'y', '\x1b'])
        self.assertIn('gone', ' '.join(screen.drawn))

    def test_control_characters(self):
        self.assertEqual(sanitize_label('a\x1b[31m\nb\u202ec'), 'a?[31m?b?c')

    def test_unicode_clipping(self):
        self.assertEqual(clip_label('界界x', 3), '界')
        self.assertEqual(clip_label('e\u0301x', 1), 'e\u0301')

    def test_small_terminal(self):
        tmux = self.adapter()
        self.run_ui(tmux, ['n', '\x1b'], size=(3, 10))
        tmux.create_session.assert_not_called()

    def test_name_field_shows_cursor_after_the_name(self):
        screen = Screen([])
        lobby = Lobby(screen, self.adapter())
        lobby.mode, lobby.name = 'new', 'bellhop-1'
        with patch('bellhop.ui.curses.curs_set') as cursor:
            lobby.draw()
        cursor.assert_called_with(1)
        self.assertIsNotNone(screen.cursor)
        row, col = screen.cursor
        name_write = next(w for w in screen.writes if w[0] == row and 'bellhop-1' in w[2])
        self.assertEqual(col, name_write[1] + name_write[2].index('bellhop-1') + 9)

    def test_long_wide_name_scrolls_to_keep_cursor_visible(self):
        screen = Screen([], size=(17, 40))
        lobby = Lobby(screen, self.adapter())
        lobby.mode, lobby.name = 'new', '界' * 70 + 'xyz'
        with patch('bellhop.ui.curses.curs_set'):
            lobby.draw()
        self.assertIsNotNone(screen.cursor)
        row, col = screen.cursor
        self.assertLess(col, 38)
        self.assertTrue(any(w[0] == row and 'xyz' in w[2] for w in screen.writes))

    def test_session_table_is_framed_with_aligned_columns(self):
        screen = Screen([])
        lobby = Lobby(screen, self.adapter([Session('$1', 'alpha', 12, 3),
                                            Session('$2', 'beta', 1, 0)]))
        lobby.refresh()
        with patch('bellhop.ui.curses.curs_set'):
            lobby.draw()
        rendered = ' '.join(screen.drawn)
        for label in ['NAME', 'WINDOWS', 'ATTACHED', '╭', '╰', '│']:
            self.assertIn(label, rendered)
        name_row = next(w[0] for w in screen.writes if 'alpha' in w[2])
        windows_header = next(w for w in screen.writes if w[2].strip() == 'WINDOWS')
        count = next(w for w in screen.writes if w[0] == name_row and w[2].strip() == '12')
        self.assertEqual(count[1], windows_header[1])

    def test_ascii_banner_is_centered_and_compacts_in_popup(self):
        large = Screen([], size=(23, 100))
        compact = Screen([], size=(17, 80))
        for screen in (large, compact):
            with patch('bellhop.ui.curses.curs_set'):
                Lobby(screen, self.adapter()).draw()
        artwork = [w for w in large.writes if '| |' in w[2]]
        self.assertGreaterEqual(len(artwork), 3)
        self.assertTrue(all(w[1] > 0 for w in artwork))
        self.assertFalse(any('| |' in w[2] for w in compact.writes))
        compact_title = next(w for w in compact.writes if w[2] == 'bellhop')
        self.assertEqual(compact_title[1], 36)

    def test_small_popup_keeps_sessions_and_name_entry_usable(self):
        for height in (9, 10, 12, 17, 24):
            for width in (38, 60, 100):
                with self.subTest(size=(height, width)):
                    screen = Screen([], size=(height, width))
                    lobby = Lobby(screen, self.adapter([Session('$1', 'alpha', 1, 0)]))
                    lobby.refresh()
                    with patch('bellhop.ui.curses.curs_set'):
                        self.assertTrue(lobby.draw())
                        name_row = next(w[0] for w in screen.writes if 'alpha' in w[2])
                        bottom = next(w[0] for w in screen.writes if w[2].startswith('╰'))
                        self.assertLess(name_row, bottom)
                        lobby.mode, lobby.name = 'new', 'edited-name'
                        lobby.draw()
                    self.assertIsNotNone(screen.cursor)
                    self.assertLess(screen.cursor[0], height)

    def test_status_message_expires(self):
        screen = Screen([])
        with patch('bellhop.ui.time.monotonic', return_value=10) as clock, \
                patch('bellhop.ui.curses.curs_set'):
            lobby = Lobby(screen, self.adapter(), 'Temporary error')
            lobby.draw()
            self.assertIn('Temporary error', ' '.join(screen.drawn))
            screen.drawn.clear()
            clock.return_value = 16
            lobby.draw()
            self.assertNotIn('Temporary error', ' '.join(screen.drawn))

    def test_scrolling_up_keeps_the_viewport_still_until_its_edge(self):
        screen = Screen([])
        sessions = [Session('$' + str(i), 'session-' + str(i).zfill(2), 1, 0) for i in range(20)]
        lobby = Lobby(screen, self.adapter(sessions))
        lobby.refresh()
        with patch('bellhop.ui.curses.curs_set'):
            lobby.index = 10
            lobby.draw()
            row = next(w[0] for w in screen.writes if 'session-05' in w[2])
            screen.writes.clear()
            lobby.index = 9
            lobby.draw()
        self.assertEqual(next(w[0] for w in screen.writes if 'session-05' in w[2]), row)

    def test_delete_does_not_backspace_at_end_of_name(self):
        lobby = Lobby(Screen([]), self.adapter())
        lobby.mode, lobby.name = 'new', 'my-name'
        lobby.name_key(curses.KEY_DC)
        self.assertEqual(lobby.name, 'my-name')
