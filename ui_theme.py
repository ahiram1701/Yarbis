import tkinter as tk


THEMES = {
    "dark": {
        "bg": "#000000",
        "panel": "#05080c",
        "panel_alt": "#08131a",
        "border": "#123746",
        "fg": "#f4fbff",
        "muted": "#8fb8c8",
        "field_bg": "#030609",
        "field_fg": "#f4fbff",
        "button_bg": "#071016",
        "button_active": "#0b1d27",
        "disabled_bg": "#05080c",
        "disabled_fg": "#51616a",
        "accent": "#00d9ff",
        "accent_hover": "#54e8ff",
        "accent_fg": "#001116",
        "secondary": "#00ff88",
        "secondary_hover": "#5dffb2",
        "secondary_fg": "#00150b",
        "danger": "#ff3b5c",
        "danger_hover": "#ff6b82",
        "danger_fg": "#190006",
        "select_bg": "#004f63",
        "select_fg": "#f4fbff",
    },
    "light": {
        "bg": "#f3ede2",
        "panel": "#fff9f1",
        "panel_alt": "#efe5d6",
        "border": "#d5c8b8",
        "fg": "#1d232b",
        "muted": "#6f6559",
        "field_bg": "#fffdfa",
        "field_fg": "#1d232b",
        "button_bg": "#ebe1d2",
        "button_active": "#dfd2c0",
        "disabled_bg": "#f0e8dd",
        "disabled_fg": "#9d958a",
        "accent": "#d97a34",
        "accent_hover": "#e38a49",
        "accent_fg": "#fff8f1",
        "secondary": "#177b4d",
        "secondary_hover": "#20915d",
        "secondary_fg": "#fffdfa",
        "danger": "#c7364d",
        "danger_hover": "#d64c61",
        "danger_fg": "#fffdfa",
        "select_bg": "#d97a34",
        "select_fg": "#fff8f1",
    },
}



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
        padx=10,
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
