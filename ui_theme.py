import tkinter as tk
from tkinter import ttk


THEMES = {
    "dark": {
        "bg": "#0f1115",
        "panel": "#171a21",
        "panel_alt": "#20242d",
        "surface": "#11141a",
        "border": "#303743",
        "fg": "#f4f6f8",
        "muted": "#a3abb7",
        "field_bg": "#0b0d11",
        "field_fg": "#f4f6f8",
        "button_bg": "#252a33",
        "button_active": "#303744",
        "disabled_bg": "#05080c",
        "disabled_fg": "#69717c",
        "accent": "#4f8cff",
        "accent_hover": "#75a4ff",
        "accent_fg": "#ffffff",
        "secondary": "#2fbf71",
        "secondary_hover": "#53d18b",
        "secondary_fg": "#07140d",
        "warning": "#d9a441",
        "warning_fg": "#1f1602",
        "danger": "#e35d6a",
        "danger_hover": "#ef7d87",
        "danger_fg": "#ffffff",
        "select_bg": "#2d5aa7",
        "select_fg": "#f4fbff",
    },
    "light": {
        "bg": "#f6f7f9",
        "panel": "#ffffff",
        "panel_alt": "#edf1f5",
        "surface": "#f0f3f7",
        "border": "#d6dde6",
        "fg": "#1f2733",
        "muted": "#667085",
        "field_bg": "#ffffff",
        "field_fg": "#1d232b",
        "button_bg": "#eef2f6",
        "button_active": "#dfe6ee",
        "disabled_bg": "#eef1f5",
        "disabled_fg": "#9aa3af",
        "accent": "#2563eb",
        "accent_hover": "#3b82f6",
        "accent_fg": "#ffffff",
        "secondary": "#178f5f",
        "secondary_hover": "#20a974",
        "secondary_fg": "#ffffff",
        "warning": "#c68418",
        "warning_fg": "#ffffff",
        "danger": "#c33b4a",
        "danger_hover": "#d34f5d",
        "danger_fg": "#ffffff",
        "select_bg": "#2563eb",
        "select_fg": "#ffffff",
    },
}

BOOTSTRAP_THEMES = {
    "dark": "darkly",
    "light": "litera",
}

SPACING = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
}


def bootstrap_theme_name(theme_name: str) -> str:
    return BOOTSTRAP_THEMES.get(theme_name, BOOTSTRAP_THEMES["dark"])


def create_app_style(root, theme_name: str):
    try:
        import ttkbootstrap as ttkbootstrap

        try:
            return ttkbootstrap.Style(master=root, theme=bootstrap_theme_name(theme_name)), True
        except TypeError:
            style = ttkbootstrap.Style(theme=bootstrap_theme_name(theme_name))
            return style, True
    except Exception:
        return ttk.Style(root), False


def use_bootstrap_theme(style, theme_name: str) -> bool:
    target_theme = bootstrap_theme_name(theme_name)
    try:
        style.theme_use(target_theme)
        return True
    except Exception:
        try:
            return style.theme_use() == target_theme
        except Exception:
            pass
        return False


def status_tone(text: object) -> str:
    lowered = str(text or "").lower()
    if any(token in lowered for token in ("error", "fall", "detenido", "falt", "sin objetivo", "bloque")):
        return "danger"
    if any(token in lowered for token in ("esperando", "pendiente", "advert", "revis")):
        return "warning"
    if any(token in lowered for token in ("activo", "lista", "listo", "ok", "usable")):
        return "success"
    return "neutral"


def compact_text(value: object, max_chars: int = 140) -> str:
    rendered = " ".join(str(value or "").split())
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max(0, max_chars - 1)].rstrip() + "..."


def create_card(parent, title: str = "", subtitle: str = ""):
    card = ttk.Frame(parent, style="Card.TFrame", padding=14)
    card.columnconfigure(0, weight=1)
    row = 0
    if title:
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
    if subtitle:
        ttk.Label(card, text=subtitle, style="Muted.TLabel", wraplength=520).grid(
            row=row,
            column=0,
            sticky="ew",
            pady=(2, 0),
        )
    return card


def create_section(parent, title: str, subtitle: str = ""):
    section = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe", padding=12)
    section.columnconfigure(0, weight=1)
    if subtitle:
        ttk.Label(section, text=subtitle, style="Muted.TLabel", wraplength=560).grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 10),
        )
    return section


def configure_app_styles(style, palette: dict):
    style.configure(".", background=palette["bg"], foreground=palette["fg"])
    style.configure("TFrame", background=palette["bg"])
    style.configure("Surface.TFrame", background=palette["surface"])
    style.configure("Card.TFrame", background=palette["panel"], borderwidth=1, relief="solid")
    style.configure("Sidebar.TFrame", background=palette["surface"])
    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
    style.configure("Card.TLabel", background=palette["panel"], foreground=palette["fg"])
    style.configure("CardTitle.TLabel", background=palette["panel"], foreground=palette["fg"], font=("Segoe UI", 11, "bold"))
    style.configure("Title.TLabel", background=palette["bg"], foreground=palette["fg"], font=("Segoe UI", 16, "bold"))
    style.configure("Subtitle.TLabel", background=palette["bg"], foreground=palette["muted"], font=("Segoe UI", 10))
    style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"])
    style.configure("SidebarTitle.TLabel", background=palette["surface"], foreground=palette["fg"], font=("Segoe UI", 16, "bold"))
    style.configure("SidebarMuted.TLabel", background=palette["surface"], foreground=palette["muted"])
    style.configure("TLabelframe", background=palette["bg"], bordercolor=palette["border"], relief="solid")
    style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["muted"], font=("Segoe UI", 10, "bold"))
    style.configure("Section.TLabelframe", background=palette["bg"], bordercolor=palette["border"], relief="solid")
    style.configure("Section.TLabelframe.Label", background=palette["bg"], foreground=palette["fg"], font=("Segoe UI", 10, "bold"))
    style.configure("TCheckbutton", background=palette["bg"], foreground=palette["fg"], padding=(6, 4))
    style.configure("TButton", background=palette["button_bg"], foreground=palette["fg"], bordercolor=palette["border"], relief="flat", padding=(12, 8))
    style.configure("Nav.TButton", background=palette["surface"], foreground=palette["muted"], bordercolor=palette["surface"], relief="flat", padding=(12, 10), anchor="w")
    style.configure("Active.Nav.TButton", background=palette["panel_alt"], foreground=palette["fg"], bordercolor=palette["accent"], relief="flat", padding=(12, 10), anchor="w")
    style.configure("Accent.TButton", background=palette["accent"], foreground=palette["accent_fg"], bordercolor=palette["accent"], relief="flat", padding=(12, 8))
    style.configure("Secondary.TButton", background=palette["secondary"], foreground=palette["secondary_fg"], bordercolor=palette["secondary"], relief="flat", padding=(12, 8))
    style.configure("Danger.TButton", background=palette["danger"], foreground=palette["danger_fg"], bordercolor=palette["danger"], relief="flat", padding=(12, 8))
    style.configure("Badge.TLabel", background=palette["panel_alt"], foreground=palette["fg"], padding=(8, 3), font=("Segoe UI", 9, "bold"))
    style.configure("Success.Badge.TLabel", background=palette["secondary"], foreground=palette["secondary_fg"], padding=(8, 3), font=("Segoe UI", 9, "bold"))
    style.configure("Warning.Badge.TLabel", background=palette["warning"], foreground=palette["warning_fg"], padding=(8, 3), font=("Segoe UI", 9, "bold"))
    style.configure("Danger.Badge.TLabel", background=palette["danger"], foreground=palette["danger_fg"], padding=(8, 3), font=("Segoe UI", 9, "bold"))
    style.configure("TEntry", fieldbackground=palette["field_bg"], foreground=palette["field_fg"], insertcolor=palette["field_fg"], bordercolor=palette["border"], padding=7)
    style.configure("TCombobox", fieldbackground=palette["field_bg"], background=palette["field_bg"], foreground=palette["field_fg"], arrowcolor=palette["field_fg"], bordercolor=palette["border"], padding=7)
    style.configure(
        "Yarbis.TSpinbox",
        fieldbackground=palette["field_bg"],
        background=palette["button_bg"],
        foreground=palette["field_fg"],
        insertcolor=palette["field_fg"],
        arrowcolor=palette["field_fg"],
        bordercolor=palette["border"],
        padding=7,
    )
    style.configure(
        "Yarbis.Vertical.TScrollbar",
        background=palette["button_bg"],
        troughcolor=palette["panel"],
        bordercolor=palette["border"],
        darkcolor=palette["button_bg"],
        lightcolor=palette["button_bg"],
        arrowcolor=palette["fg"],
        relief="flat",
        borderwidth=0,
        arrowsize=12,
        width=14,
    )

    style.map(
        "TButton",
        background=[("active", palette["button_active"]), ("disabled", palette["disabled_bg"])],
        foreground=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "Nav.TButton",
        background=[("active", palette["panel_alt"]), ("disabled", palette["surface"])],
        foreground=[("active", palette["fg"]), ("disabled", palette["disabled_fg"])],
    )
    style.map(
        "Accent.TButton",
        background=[("active", palette["accent_hover"]), ("disabled", palette["disabled_bg"])],
        foreground=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "Secondary.TButton",
        background=[("active", palette["secondary_hover"]), ("disabled", palette["disabled_bg"])],
        foreground=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "Danger.TButton",
        background=[("active", palette["danger_hover"]), ("disabled", palette["disabled_bg"])],
        foreground=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", palette["disabled_bg"])],
        foreground=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["field_bg"]), ("disabled", palette["disabled_bg"])],
        foreground=[("readonly", palette["field_fg"]), ("disabled", palette["disabled_fg"])],
        selectbackground=[("readonly", palette["select_bg"])],
        selectforeground=[("readonly", palette["select_fg"])],
        arrowcolor=[("disabled", palette["disabled_fg"])],
    )
    style.map(
        "Yarbis.Vertical.TScrollbar",
        background=[("active", palette["button_active"]), ("pressed", palette["accent"]), ("disabled", palette["disabled_bg"])],
        arrowcolor=[("pressed", palette["accent_fg"]), ("disabled", palette["disabled_fg"])],
    )


def style_scrollbar_widget(scrollbar, palette: dict):
    try:
        scrollbar.configure(style="Yarbis.Vertical.TScrollbar")
    except tk.TclError:
        try:
            scrollbar.configure(
                bg=palette["button_bg"],
                activebackground=palette["button_active"],
                troughcolor=palette["panel"],
                highlightbackground=palette["panel"],
                bd=0,
                relief="flat",
                width=14,
            )
        except tk.TclError:
            pass


def style_text_widget(widget, palette: dict):
    widget.configure(
        bg=palette["field_bg"],
        fg=palette["field_fg"],
        insertbackground=palette["field_fg"],
        selectbackground=palette["select_bg"],
        selectforeground=palette["select_fg"],
        inactiveselectbackground=palette["select_bg"],
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
        relief="flat",
        bd=0,
        padx=12,
        pady=10,
    )

    try:
        widget.configure(disabledforeground=palette["field_fg"])
    except tk.TclError:
        pass

    frame = getattr(widget, "frame", None)
    if frame is not None:
        frame.configure(bg=palette["panel"], bd=0, highlightthickness=0)

    vbar = getattr(widget, "vbar", None)
    if vbar is not None:
        style_scrollbar_widget(vbar, palette)


def style_listbox_widget(widget, palette: dict):
    widget.configure(
        bg=palette["field_bg"],
        fg=palette["field_fg"],
        selectbackground=palette["select_bg"],
        selectforeground=palette["select_fg"],
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
        relief="flat",
        bd=0,
    )
