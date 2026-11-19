import tkinter as tk

from confortimetro.gui import theme


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_cores_passam_wcag():
    c = theme.COLORS
    assert _ratio("#ffffff", c["primary_h"]) >= 4.5
    assert _ratio("#ffffff", c["warn"]) >= 4.5
    assert _ratio(c["warn"], c["surface"]) >= 4.5
    assert _ratio(c["accent"], c["bg"]) >= 4.5
    assert _ratio(c["line"], c["surface"]) >= 3
    assert _ratio(c["scroll"], c["surface_2"]) >= 3


def test_botao_ativa_por_teclado_e_mostra_foco():
    root = tk.Tk()
    try:
        theme.apply_theme(root)
        calls = []
        button = theme.RoundedButton(root, "Ok", command=lambda: calls.append(1))
        button.pack()
        root.update()
        button.focus_force()
        root.update()
        assert button._focused
        # O Tk do Windows entrega o Enter do teclado numérico como <Return>.
        keys = ["<Return>", "<space>"]
        if root.tk.call("tk", "windowingsystem") == "x11":
            keys.append("<KP_Enter>")
        for key in keys:
            button.event_generate(key)
        assert len(calls) == len(keys)
        button.configure(state="disabled")
        button.event_generate("<space>")
        assert len(calls) == len(keys)
        button.event_generate("<FocusOut>")
        assert not button._focused
    finally:
        root.destroy()
