"""Theme stylesheets for the Qt UI.

Dark and light variants share the original purple/blue palette.
The Rekordbox variant uses a near-black DJ-software style with dark
surfaces, subtle borders, and a stronger blue/purple accent.
"""

FONT_STACK = "'Inter', 'Segoe UI Variable Text', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"


# name -> (
#     background,
#     surface/panel,
#     alternate surface,
#     border,
#     accent,
#     strong accent,
#     text,
#     muted text,
#     icon,
#     selection
# )

PALETTES = {

    # ------------------------------------------------------------------
    # Original dark theme
    # ------------------------------------------------------------------

    "dark": {
        "bg": "#33334d",
        "surface": "#505081",
        "surface_alt": "#454572",
        "border": "#6a6aa0",
        "accent": "#8686AC",
        "accent_strong": "#9a9ac2",
        "text": "#ffffff",
        "muted_text": "#d8d8ec",
        "icon": "#ffffff",
        "selection": "#8686AC",
    },

    # ------------------------------------------------------------------
    # Original light theme
    # ------------------------------------------------------------------

    "light": {
        "bg": "#f4f4fa",
        "surface": "#ffffff",
        "surface_alt": "#e8e8f5",
        "border": "#c3c3dd",
        "accent": "#1677FF",
        "accent_strong": "#2F8BFF",
        "text": "#2c2c44",
        "muted_text": "#50506e",
        "icon": "#2c2c44",
        "selection": "#8686AC",
    },

    # ------------------------------------------------------------------
    # Rekordbox-style dark theme
    # ------------------------------------------------------------------

    "rekordbox": {
    "bg": "#0D0E10",
    "surface": "#151719",
    "surface_alt": "#202225",
    "border": "#303236",
    "accent": "#1677FF",
    "accent_strong": "#2F8BFF",
    "text": "#E8E9EB",
    "muted_text": "#85888D",
    "icon": "#C8C9CC",
    "selection": "#173A63",
    },
    
    
}


def icon_color(mode: str) -> str:
    """Return the icon color for the selected theme."""

    return PALETTES.get(
        mode,
        PALETTES["dark"],
    )["icon"]


def build_stylesheet(mode: str) -> str:
    """Build the application-wide Qt stylesheet for a theme."""

    p = PALETTES.get(
        mode,
        PALETTES["dark"],
    )

    return f"""
/* ================================================================
   Global
   ================================================================ */

* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {p['text']};
}}

QMainWindow, QWidget {{
    background-color: {p['bg']};
}}

QToolTip {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    padding: 5px 7px;
}}


/* ================================================================
   Tabs
   ================================================================ */

QTabWidget::pane {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    background: {p['bg']};
}}

QTabBar::tab {{
    background: {p['surface']};
    color: {p['muted_text']};
    padding: 8px 18px;
    border: 1px solid {p['border']};
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}

QTabBar::tab:hover {{
    background-color: {p['surface_alt']};
    color: {p['text']};
}}

QTabBar::tab:selected {{
    background: {p['accent']};
    color: #ffffff;
    border-color: {p['accent']};
}}


/* ================================================================
   Group boxes
   ================================================================ */

QGroupBox {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    color: {p['accent_strong']};
    font-weight: bold;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 5px;
    background-color: {p['bg']};
}}


/* ================================================================
   Buttons
   ================================================================ */

QPushButton {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 6px 14px;
    color: {p['text']};
}}

QPushButton:hover {{
    background-color: {p['surface_alt']};
    border-color: {p['accent']};
    color: #ffffff;
}}

QPushButton:pressed {{
    background-color: {p['accent']};
    border-color: {p['accent']};
}}

QPushButton:disabled {{
    color: {p['muted_text']};
    background-color: {p['surface_alt']};
    border-color: {p['border']};
}}


/* ================================================================
   Generate / primary button
   ================================================================ */

QPushButton#generateButton {{
    background-color: {p['accent_strong']};
    border: 1px solid {p['accent_strong']};
    border-radius: 5px;
    color: #ffffff;
    font-weight: bold;
    font-size: 14px;
    padding: 8px 28px;
}}

QPushButton#generateButton:hover {{
    background-color: {p['accent']};
    border-color: {p['accent']};
}}

QPushButton#generateButton:pressed {{
    background-color: {p['selection']};
    border-color: {p['selection']};
}}


/* ================================================================
   Inputs
   ================================================================ */

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QListWidget,
QTableWidget,
QTextEdit {{
    background-color: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 4px;
    selection-background-color: {p['selection']};
    selection-color: #ffffff;
    gridline-color: {p['border']};
}}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QListWidget:focus,
QTableWidget:focus,
QTextEdit:focus {{
    border-color: {p['accent']};
}}


/* ================================================================
   Combo boxes
   ================================================================ */

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    selection-background-color: {p['selection']};
    selection-color: #ffffff;
}}


/* ================================================================
   Tables
   ================================================================ */

QTableWidget {{
    alternate-background-color: {p['surface']};
}}

QTableWidget::item {{
    padding: 3px;
}}

QTableWidget::item:selected {{
    background-color: {p['selection']};
    color: #ffffff;
}}

QTableWidget::item:hover {{
    background-color: {p['surface_alt']};
}}

QHeaderView::section {{
    background-color: {p['surface']};
    color: {p['muted_text']};
    padding: 6px;
    border: 1px solid {p['border']};
    font-weight: bold;
}}

QHeaderView::section:hover {{
    background-color: {p['surface_alt']};
    color: {p['text']};
}}


/* ================================================================
   Scrollbars
   ================================================================ */

QScrollBar:vertical {{
    background: {p['bg']};
    width: 10px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: {p['bg']};
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {{
    background: {p['border']};
    border-radius: 4px;
    min-height: 25px;
    min-width: 25px;
}}

QScrollBar::handle:hover {{
    background: {p['accent']};
}}

QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {{
    background: none;
    border: none;
}}


/* ================================================================
   Sliders
   ================================================================ */

QSlider::groove:horizontal {{
    background: {p['surface_alt']};
    height: 5px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {p['accent_strong']};
    width: 13px;
    height: 13px;
    margin: -4px 0;
    border-radius: 6px;
}}

QSlider::handle:horizontal:hover {{
    background: {p['accent']};
}}

QSlider::sub-page:horizontal {{
    background: {p['accent']};
    border-radius: 2px;
}}


/* ================================================================
   Labels
   ================================================================ */

QLabel {{
    color: {p['text']};
}}

QLabel[secondary="true"] {{
    color: {p['muted_text']};
}}


/* ================================================================
   Checkboxes
   ================================================================ */

QCheckBox {{
    spacing: 6px;
    color: {p['text']};
}}

QCheckBox:hover {{
    color: #ffffff;
}}


/* ================================================================
   Menus
   ================================================================ */

QMenuBar {{
    background-color: {p['surface']};
    color: {p['text']};
    border-bottom: 1px solid {p['border']};
}}

QMenuBar::item {{
    background: transparent;
    padding: 5px 9px;
}}

QMenuBar::item:selected {{
    background-color: {p['accent']};
    color: #ffffff;
}}

QMenu {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 22px 6px 10px;
}}

QMenu::item:selected {{
    background-color: {p['accent']};
    color: #ffffff;
}}

QMenu::separator {{
    height: 1px;
    background-color: {p['border']};
    margin: 4px 6px;
}}


/* ================================================================
   Progress bars
   ================================================================ */

QProgressBar {{
    border: 1px solid {p['border']};
    border-radius: 4px;
    text-align: center;
    background-color: {p['surface_alt']};
    color: {p['text']};
}}

QProgressBar::chunk {{
    background-color: {p['accent']};
    border-radius: 3px;
}}


/* ================================================================
   Status bar
   ================================================================ */

QStatusBar {{
    background-color: {p['surface']};
    color: {p['muted_text']};
    border-top: 1px solid {p['border']};
}}

QStatusBar::item {{
    border: none;
}}


/* ================================================================
   Toolbars
   ================================================================ */

QToolBar {{
    background-color: {p['surface']};
    border: none;
    spacing: 4px;
}}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px;
}}

QToolButton:hover {{
    background-color: {p['surface_alt']};
    border-color: {p['border']};
}}

QToolButton:pressed {{
    background-color: {p['selection']};
}}


/* ================================================================
   Splitters
   ================================================================ */

QSplitter::handle {{
    background-color: {p['border']};
}}

QSplitter::handle:hover {{
    background-color: {p['accent']};
}}
"""


# ------------------------------------------------------------------
# Backward-compatible default export
# ------------------------------------------------------------------

DARCULA_QSS = build_stylesheet("dark")

# Rekordbox-style dark stylesheet.
REKORDBOX_QSS = build_stylesheet("rekordbox")