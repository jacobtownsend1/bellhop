"""Render Bellhop's session lobby."""

import curses
from dataclasses import dataclass
import os
import sys
import time
import unicodedata

from bellhop.tmux import TmuxError

BANNER = (
    r" _          _ _ _",
    r"| |__   ___| | | |__   ___  _ __",
    r"| '_ \ / _ \ | | '_ \ / _ \| '_ " + "\\",
    r"| |_) |  __/ | | | | | (_) | |_) |",
    r"|_.__/ \___|_|_|_| |_|\___/| .__/",
    r"                          |_|",
)


@dataclass(frozen=True)
class Theme:
    accent: int = curses.A_BOLD
    border: int = curses.A_DIM
    key: int = curses.A_BOLD
    attached: int = curses.A_BOLD
    danger: int = curses.A_BOLD
    selected: int = curses.A_REVERSE | curses.A_BOLD
    muted: int = curses.A_DIM


def make_theme():
    """Use the terminal's background, with a monochrome fallback."""
    fallback = Theme()
    if os.environ.get('NO_COLOR') or not curses.has_colors():
        return fallback
    try:
        curses.start_color()
        curses.use_default_colors()
        for pair, foreground, background in ((1, curses.COLOR_CYAN, -1),
                                              (2, curses.COLOR_YELLOW, -1),
                                              (3, curses.COLOR_GREEN, -1),
                                              (4, curses.COLOR_RED, -1),
                                              (5, curses.COLOR_WHITE, curses.COLOR_BLUE)):
            curses.init_pair(pair, foreground, background)
        return Theme(accent=curses.color_pair(1) | curses.A_BOLD,
                     border=curses.color_pair(1), key=curses.color_pair(2) | curses.A_BOLD,
                     attached=curses.color_pair(3), danger=curses.color_pair(4) | curses.A_BOLD,
                     selected=curses.color_pair(5) | curses.A_BOLD)
    except curses.error:
        return fallback


def sanitize_label(text):
    return ''.join('?' if not c.isprintable() else c for c in text)


def clip_label(text, columns):
    result = []
    used = 0
    for char in sanitize_label(text):
        width = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1)
        if used + width > columns:
            break
        result.append(char)
        used += width
    return ''.join(result)


def text_width(text):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
               for c in sanitize_label(text))


class Lobby:
    def __init__(self, screen, tmux, message='', theme=None):
        self.screen = screen
        self.tmux = tmux
        self.sessions = []
        self.index = 0
        self.message = message
        self.message_until = time.monotonic() + 5 if message else 0
        self.mode = 'lobby'
        self.name = ''
        self.pending_delete = None
        self.last_refresh = 0
        self.theme = theme or Theme()
        self.cursor_visible = None
        self.scroll_top = 0

    def set_message(self, message):
        self.message = message
        self.message_until = time.monotonic() + 5

    def refresh(self):
        selected = self.sessions[self.index].id if self.sessions else None
        try:
            self.sessions = self.tmux.list_sessions()
            self.index = next((i for i, s in enumerate(self.sessions) if s.id == selected),
                              min(self.index, max(0, len(self.sessions) - 1)))
        except TmuxError as error:
            self.set_message(str(error))
        self.last_refresh = time.monotonic()

    def write(self, row, text, attr=0, col=0, columns=None):
        height, width = self.screen.getmaxyx()
        if 0 <= row < height and 0 <= col < width - 1:
            try:
                available = width - 1 - col
                if columns is not None:
                    available = min(available, columns)
                self.screen.addstr(row, col, clip_label(text, available), attr)
            except curses.error:
                # A resize can happen between measuring and drawing.
                pass

    def cursor(self, visible, position=None):
        if visible != self.cursor_visible:
            try:
                curses.curs_set(1 if visible else 0)
            except curses.error:
                pass
            self.cursor_visible = visible
        if visible and position is not None:
            try:
                self.screen.move(*position)
            except curses.error:
                pass

    def frame(self, top, bottom, title, attr, bottom_label=''):
        _, width = self.screen.getmaxyx()
        right = width - 2
        self.write(top, '╭' + '─' * (width - 4) + '╮', attr, col=1)
        self.write(top, ' ' + title + ' ', attr, col=3, columns=width - 8)
        for row in range(top + 1, bottom):
            self.write(row, '│', attr, col=1)
            self.write(row, '│', attr, col=right)
        self.write(bottom, '╰' + '─' * (width - 4) + '╯', attr, col=1)
        if bottom_label:
            self.write(bottom, ' ' + bottom_label + ' ', attr, col=3, columns=width - 8)

    def hints(self, row, groups):
        col = 2
        _, width = self.screen.getmaxyx()
        for label, attr in groups:
            if col + text_width(label) > width - 2:
                break
            self.write(row, label, attr, col=col)
            col += text_width(label) + 2

    def name_field(self, height, width, compressed=False):
        top = height - (4 if compressed else 5)
        self.frame(top, top + 2, 'New session · edit name', self.theme.border)
        field_col, field_width = 4, width - 8
        shown = sanitize_label(self.name)
        while text_width(shown) > field_width - 1:
            shown = shown[1:]
        while shown and unicodedata.combining(shown[0]):
            shown = shown[1:]
        self.write(top + 1, shown + ' ' * (field_width - text_width(shown)),
                   self.theme.accent, col=field_col, columns=field_width)
        groups = [('Enter create', self.theme.key)]
        if width >= 50:
            groups.append(('Ctrl-U clear', self.theme.muted))
        groups.append(('Esc cancel', self.theme.muted))
        self.hints(top + 3, groups)
        self.cursor(True, (top + 1, field_col + text_width(shown)))

    def draw(self):
        if self.message and time.monotonic() >= self.message_until:
            self.message = ''
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        if height < 9 or width < 38:
            self.write(0, 'Resize terminal; Esc exits')
            self.cursor(False)
            self.screen.refresh()
            return False
        banner_width = max(map(len, BANNER))
        expanded = height >= 22 and width >= 56
        compressed = height < 13
        if expanded:
            col = (width - banner_width) // 2
            for row, line in enumerate(BANNER):
                self.write(row, line.ljust(banner_width), self.theme.accent, col=col)
            subtitle_row, top = 7, 9
        elif not compressed:
            self.write(0, 'bellhop', self.theme.accent, col=(width - 7) // 2)
            subtitle_row, top = 1, 3
        else:
            title = 'bellhop · tmux lobby'
            self.write(0, title, self.theme.accent, col=(width - len(title)) // 2)
            top = 1
        if not compressed:
            subtitle = 'tmux lobby'
            self.write(subtitle_row, subtitle, self.theme.muted,
                       col=(width - len(subtitle)) // 2)
        total = len(self.sessions)
        bottom = height - (5 if compressed else 6)
        self.frame(top, bottom, 'Sessions ({})'.format(total), self.theme.border)
        name_col, windows_col, attached_col = 3, width - 23, width - 12
        self.write(top + 1, 'NAME', self.theme.accent, col=name_col + 2)
        self.write(top + 1, 'WINDOWS', self.theme.accent, col=windows_col)
        self.write(top + 1, 'ATTACHED', self.theme.accent, col=attached_col)
        first_row = top + 2
        if bottom - top >= 4:
            self.write(first_row, '├' + '─' * (width - 4) + '┤', self.theme.border, col=1)
            first_row += 1
        capacity = max(0, bottom - first_row)
        if self.index < self.scroll_top:
            self.scroll_top = self.index
        elif self.index >= self.scroll_top + capacity:
            self.scroll_top = self.index - capacity + 1
        self.scroll_top = min(max(0, self.scroll_top), max(0, total - capacity))
        start = self.scroll_top
        if not self.sessions:
            self.write(first_row, 'No sessions yet · n creates one', self.theme.muted,
                       col=name_col, columns=width - 6)
        for offset, session in enumerate(self.sessions[start:start + capacity]):
            active = offset + start == self.index
            row = first_row + offset
            attr = self.theme.selected if active else 0
            if active:
                self.write(row, ' ' * (width - 4), attr, col=2)
            label = ('> ' if active else '  ') + session.name
            self.write(row, label, attr, col=name_col, columns=windows_col - name_col - 2)
            self.write(row, str(session.windows).rjust(7), attr, col=windows_col, columns=7)
            clients_attr = attr if active else (self.theme.attached if session.clients else self.theme.muted)
            self.write(row, str(session.clients).rjust(8), clients_attr, col=attached_col, columns=8)
        if self.mode == 'new':
            self.name_field(height, width, compressed)
        elif self.mode == 'delete':
            prompt_top = height - (4 if compressed else 5)
            self.frame(prompt_top, prompt_top + 2, 'Delete session?', self.theme.danger,
                       bottom_label='Kills all its programs')
            self.write(prompt_top + 1, self.pending_delete.name, self.theme.danger,
                       col=4, columns=width - 8)
            self.hints(prompt_top + 3, [('y confirm', self.theme.danger),
                                    ('any other key cancels', self.theme.muted)])
            self.cursor(False)
        else:
            self.write(height - 4, self.message, self.theme.key, col=2)
            navigation = [('↑↓/jk move', self.theme.muted)]
            if width >= 60:
                navigation.append(('hl first/last', self.theme.muted))
            navigation.append(('Enter attach', self.theme.accent))
            self.hints(height - 3, navigation)
            actions = [('n new', self.theme.key), ('d delete', self.theme.danger)]
            if width >= 48:
                actions.append(('r refresh', self.theme.accent))
            actions.append(('Esc exit', self.theme.muted))
            self.hints(height - 2, actions)
            self.cursor(False)
        self.screen.refresh()
        return True

    def name_key(self, key):
        if key == '\x1b':
            self.mode = 'lobby'
        elif key in ('\n', '\r', curses.KEY_ENTER):
            try:
                target = self.tmux.create_session(self.name, os.getcwd())
                return target.id
            except TmuxError as error:
                self.set_message(str(error))
                self.mode = 'lobby'
                self.refresh()
        elif key == '\x15':
            self.name = ''
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            self.name = self.name[:-1]
        elif isinstance(key, str) and key.isprintable() and len(self.name) < 80:
            self.name += key
        return None

    def run(self):
        self.screen.keypad(True)
        self.screen.timeout(100)
        self.refresh()
        while True:
            if self.mode == 'lobby' and time.monotonic() - self.last_refresh >= .5:
                self.refresh()
            usable = self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_RESIZE:
                continue
            if not usable:
                if key == '\x1b':
                    return None
                continue
            if self.mode == 'new':
                target = self.name_key(key)
                if target is not None:
                    return target
                continue
            if self.mode == 'delete':
                if key == 'y':
                    try:
                        self.tmux.kill_session(self.pending_delete)
                        self.set_message('Session deleted.')
                    except TmuxError as error:
                        self.set_message(str(error))
                self.mode = 'lobby'
                self.pending_delete = None
                self.refresh()
                continue
            if key == '\x1b':
                return None
            if key in ('j', curses.KEY_DOWN):
                self.index = min(self.index + 1, max(0, len(self.sessions) - 1))
            elif key in ('k', curses.KEY_UP):
                self.index = max(self.index - 1, 0)
            elif key == 'h':
                self.index = 0
            elif key == 'l':
                self.index = max(0, len(self.sessions) - 1)
            elif key == 'r':
                self.refresh()
            elif key == 'n':
                try:
                    self.name = self.tmux.default_name()
                    self.mode = 'new'
                except TmuxError as error:
                    self.set_message(str(error))
            elif self.sessions and key in ('d', curses.KEY_DC):
                self.pending_delete = self.sessions[self.index]
                self.mode = 'delete'
            elif self.sessions and key in ('\n', '\r', curses.KEY_ENTER):
                return self.sessions[self.index].id


def run_lobby(tmux, message=''):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('The lobby needs an interactive terminal.')
    if os.environ.get('TERM', '') in ('', 'dumb'):
        raise RuntimeError('Set TERM to a usable terminal type.')
    # Fail before changing terminal modes if tmux is missing/inaccessible.
    tmux.list_sessions()
    if hasattr(curses, 'set_escdelay'):
        curses.set_escdelay(25)
    try:
        return curses.wrapper(lambda screen: Lobby(screen, tmux, message, make_theme()).run())
    except curses.error as error:
        raise RuntimeError('Could not initialize this terminal: ' + str(error)) from error
