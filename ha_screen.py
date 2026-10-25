#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Painel do Home Assistant para telas Turing Smart Screen 3.5" (480x320, deitada).

Usa a biblioteca de comunicacao do projeto turing-smart-screen-python
(https://github.com/mathoudebine/turing-smart-screen-python), apontada no .ini.

    python ha_screen.py              roda e envia o painel para a tela
    python ha_screen.py --preview    gera os preview*.png com dados de exemplo (sem tela/HA)
    python ha_screen.py --config X   usa outro arquivo de configuracao
"""
import argparse
import configparser
import colorsys
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 480, 320
HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------- cores
BG = (13, 14, 19)
TILE = (29, 31, 40)
TXT = (240, 241, 245)
MUTED = (140, 145, 160)
GREEN = (74, 207, 120)
RED = (240, 84, 84)
ORANGE = (247, 160, 60)
BLUE = (88, 160, 245)
PURPLE = (176, 124, 245)
YELLOW = (255, 201, 71)
DARK = (20, 20, 20)
OFF_DOT = (70, 72, 82)

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

FONT_DIR = HERE  # ajustado em load_config(): <projeto>/res/fonts/roboto
_fonts = {}


def font(size, bold=True):
    key = (size, bold)
    if key not in _fonts:
        name = "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf"
        _fonts[key] = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    return _fonts[key]


# ------------------------------------------------------------ configuracao
class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str  # preserva maiusculas nos nomes
        if not cp.read(path, encoding="utf-8"):
            sys.exit(f"Arquivo de configuracao nao encontrado: {path}\n"
                     "Copie ha_screen.example.ini para ha_screen.ini e edite.")
        ha, tela, ent = cp["ha"], cp["tela"], cp["entidades"]
        self.url = ha["url"].rstrip("/")
        self.token = ha["token"].strip()
        self.every = max(1, int(ha.get("atualizar_seg", "2")))
        self.project = os.path.abspath(os.path.join(os.path.dirname(path), tela.get("projeto", ".")))
        self.port = tela.get("porta", "AUTO")
        self.revision = tela.get("revisao", "A").upper()
        self.brightness = int(tela.get("brilho", "30"))
        self.internet = ent.get("internet", "").strip()
        self.phone_name = ent.get("celular_nome", "Celular")
        self.phone_level = ent.get("celular_bateria", "").strip()
        self.phone_state = ent.get("celular_carregando", "").strip()
        self.phone_alert_level = int(ent.get("celular_alerta_bateria", "10"))
        alerts = cp["alertas"] if cp.has_section("alertas") else {}
        self.alert_duration = max(1, int(alerts.get("duracao_seg", "7")))
        self.alert_repeat = max(1, int(alerts.get("repetir_min", "30"))) * 60
        self.lights = [(k.strip(), v.strip()) for k, v in cp["luzes"].items()][:4] \
            if cp.has_section("luzes") else []
        self.cameras = [(k.strip(), v.strip()) for k, v in cp["cameras"].items()] \
            if cp.has_section("cameras") else []
        sp = cp["player"] if cp.has_section("player") else {}
        self.player = sp.get("entidade", "").strip()
        self.show_paused = sp.get("mostrar_pausado", "nao").strip().lower() in ("sim", "s", "true", "1", "yes")
        self.player_mode = "tela_cheia" if sp.get("modo", "mini").strip().lower() in (
            "tela_cheia", "cheia", "full") else "mini"
        ts = cp["tailscale"] if cp.has_section("tailscale") else {}
        self.tailscale = ts.get("ativar", "sim").strip().lower() in ("sim", "s", "true", "1", "yes")
        self.tailscale_cmd = ts.get("comando", "").strip()


# --------------------------------------------------------------- dados HA
_art_cache = {}  # url -> Image (so baixa a capa quando a musica muda)


def get_art(cfg, path):
    if not path:
        return None
    if path not in _art_cache:
        if len(_art_cache) > 8:
            _art_cache.clear()
        try:
            url = path if path.startswith("http") else cfg.url + path
            hdr = {} if path.startswith("http") else {"Authorization": f"Bearer {cfg.token}"}
            r = requests.get(url, headers=hdr, timeout=8)
            r.raise_for_status()
            _art_cache[path] = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            print("capa:", e)
            _art_cache[path] = None
    return _art_cache[path]


def fetch(cfg):
    r = requests.get(f"{cfg.url}/api/states",
                     headers={"Authorization": f"Bearer {cfg.token}"}, timeout=8)
    r.raise_for_status()
    st = {s["entity_id"]: s for s in r.json()}

    def state(eid):
        return st.get(eid, {}).get("state", "unavailable") if eid else "unavailable"

    def light_rgb(attributes):
        """Retorna a cor real anunciada pelo HA, em RGB, se a lâmpada for colorida."""
        rgb = attributes.get("rgb_color")
        try:
            if rgb and len(rgb) >= 3:
                return tuple(max(0, min(255, int(round(float(v))))) for v in rgb[:3])
            hs = attributes.get("hs_color")
            if hs and len(hs) >= 2:
                r, g, b = colorsys.hsv_to_rgb((float(hs[0]) % 360) / 360,
                                               max(0, min(100, float(hs[1]))) / 100, 1)
                return tuple(int(round(v * 255)) for v in (r, g, b))
        except (TypeError, ValueError, OverflowError):
            return None
        return None

    lights = []
    alerts = []
    for eid, name in cfg.lights:
        s = st.get(eid)
        on = bool(s) and s["state"] == "on"
        pct = None
        if on and s["attributes"].get("brightness") is not None:
            pct = round(s["attributes"]["brightness"] / 2.55)
        ok = bool(s) and s["state"] not in ("unavailable", "unknown")
        rgb = light_rgb(s.get("attributes", {})) if s else None
        lights.append({"name": name, "on": on, "pct": pct, "ok": ok, "rgb": rgb})
        if not ok:
            alerts.append(f"{name}: indisponível")

    for eid, name in cfg.cameras:
        s = st.get(eid)
        if not s or s["state"] in ("unavailable", "unknown"):
            alerts.append(f"{name}: câmera indisponível")

    people = sorted((s["attributes"].get("friendly_name", eid.split(".", 1)[1]).split()[0],
                     s["state"] == "home")
                    for eid, s in st.items() if eid.startswith("person."))

    try:
        level = int(float(state(cfg.phone_level)))
    except ValueError:
        level = None

    net = {"on": True, "off": False}.get(state(cfg.internet))
    if level is not None and level <= cfg.phone_alert_level and not (state(cfg.phone_state) == "charging"):
        alerts.append(f"{cfg.phone_name}: bateria em {level}%")

    media = None
    s = st.get(cfg.player) if cfg.player else None
    if s and (s["state"] == "playing" or (cfg.show_paused and s["state"] == "paused")):
        a = s["attributes"]
        media = {"title": a.get("media_title") or a.get("app_name") or "—",
                 "artist": a.get("media_artist") or "", "album": a.get("media_album_name") or "",
                 "paused": s["state"] == "paused", "key": a.get("entity_picture") or "",
                 "art": get_art(cfg, a.get("entity_picture"))}
    return {
        "media": media,
        "lights": lights, "people": people, "net": net, "bat": level,
        "alerts": alerts,
        "charging": state(cfg.phone_state) == "charging",
        "updates": sum(1 for e, s in st.items() if e.startswith("update.") and s["state"] == "on"),
        "tailscale": tailscale_info(cfg.tailscale_cmd) if (cfg.tailscale and not media) else None,
        "player_mode": cfg.player_mode,
        # quais blocos mostrar (so os configurados)
        "has_net": bool(cfg.internet), "has_phone": bool(cfg.phone_level),
        "phone_name": cfg.phone_name,
    }


# ----------------------------------------------------------------- desenho
def tailscale_info(cmd=""):
    """Le o estado do Tailscale local pela CLI (`tailscale status --json`)."""
    exe = cmd or shutil.which("tailscale")
    if not exe:
        for p in (r"C:\Program Files\Tailscale\tailscale.exe",
                  r"C:\Program Files (x86)\Tailscale\tailscale.exe",
                  "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return {"state": "missing"}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=6,
                             creationflags=0x08000000 if os.name == "nt" else 0)  # sem janela
        j = json.loads(out.stdout)
    except Exception:
        return {"state": "error"}
    peers = j.get("Peer") or {}
    ips = (j.get("Self") or {}).get("TailscaleIPs") or []
    return {"state": j.get("BackendState", "?"), "ip": ips[0] if ips else "",
            "online": sum(1 for q in peers.values() if q.get("Online")), "total": len(peers)}


def tailscale_tile(ts):
    """(rotulo, valor, cor) do bloco Tailscale."""
    st = ts["state"]
    if st == "Running":
        return (f"Tailscale · {ts['ip']}", f"{ts['online']} de {ts['total']} online", GREEN)
    return ("Tailscale", {"Stopped": "Desconectado", "NeedsLogin": "Precisa entrar",
                          "missing": "não instalado", "error": "sem resposta"}.get(st, st),
            RED if st == "Stopped" else (MUTED if st == "missing" else ORANGE))


def ellipsize(d, text, f, max_w):
    if d.textlength(text, font=f) <= max_w:
        return text
    while text and d.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def mini_player(img, x, y, w, h, media):
    """Bloco com miniatura da capa + musica/artista (sem barra de progresso)."""
    d = ImageDraw.Draw(img)
    acc = accent_from(media["art"])
    d.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=TILE)
    t = h - 16
    thumb = Image.new("RGB", (t, t), (45, 49, 60))
    if media["art"] is not None:
        thumb = media["art"].resize((t, t), Image.LANCZOS)
    else:
        ImageDraw.Draw(thumb).ellipse((t // 2 - 9, t // 2 - 9, t // 2 + 9, t // 2 + 9), fill=acc)
    mask = Image.new("L", (t, t), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, t - 1, t - 1), radius=8, fill=255)
    img.paste(thumb, (x + 8, y + 8), mask)
    tx, tw = x + 8 + t + 10, w - t - 8 - 10 - 10
    d.text((tx, y + 11), ellipsize(d, media["title"], font(16), tw), font=font(16),
           fill=(190, 192, 200) if media["paused"] else (255, 255, 255))
    sub = media["artist"] or media["album"] or ("pausado" if media["paused"] else "")
    d.text((tx, y + 35), ellipsize(d, sub, font(13, False), tw), font=font(13, False),
           fill=tuple(int(c * 0.55 + 255 * 0.45) for c in acc))
    d.rounded_rectangle((x + w - 5, y + 14, x + w - 3, y + h - 14), radius=1, fill=acc)


def fit(d, text, f, max_w):
    while text and d.textlength(text, font=f) > max_w:
        text = text[:-1]
    return text


def tile(d, x, y, w, h, label, value, color):
    d.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=TILE)
    d.rounded_rectangle((x + 8, y + 12, x + 11, y + h - 12), radius=2, fill=color)
    d.text((x + 20, y + 9), fit(d, label, font(12, False), w - 28), font=font(12, False), fill=MUTED)
    for size in (22, 20, 18, 16):  # encolhe a fonte ate caber
        f = font(size)
        if d.textlength(value, font=f) <= w - 28:
            break
    d.text((x + 20, y + h - 12), value, font=f, fill=TXT, anchor="ls")


def render(data, online=True, now=None):
    now = now or datetime.now()
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((16, 4), now.strftime("%H:%M"), font=font(44), fill=TXT)
    d.text((18, 54), f"{DIAS[now.weekday()]}, {now.day} {MESES[now.month - 1]}",
           font=font(14, False), fill=MUTED)
    if not online:
        d.text((W - 16, 14), "sem conexão com o HA", font=font(14), fill=RED, anchor="ra")
    if not data:
        return img

    if online:
        y = 12
        for name, home in data["people"][:3]:
            d.ellipse((300, y + 5, 310, y + 15), fill=GREEN if home else OFF_DOT)
            d.text((318, y), name, font=font(16), fill=TXT)
            d.text((W - 16, y + 2), "em casa" if home else "fora", font=font(13, False),
                   fill=GREEN if home else MUTED, anchor="ra")
            y += 22

    lights = data["lights"]
    n_on = sum(1 for l in lights if l["on"])
    items = []
    if lights:
        items.append(("Luzes", f"{n_on} ligadas", YELLOW if n_on else MUTED))
    if data["has_net"]:
        net = data["net"]
        items.append(("Internet", "Online" if net else ("Offline" if net is not None else "—"),
                      GREEN if net else RED))
    if data["has_phone"]:
        bat = data["bat"]
        col = GREEN if data["charging"] else (RED if bat is not None and bat <= 20 else BLUE)
        items.append((data["phone_name"], f"{bat}%" if bat is not None else "—", col))
    items.append(("Updates", f"{data['updates']} pendentes", ORANGE if data["updates"] else GREEN))

    # espaco largo: mini-player se estiver tocando; senao, info do Tailscale
    slot = None
    if data.get("media") and data.get("player_mode") != "tela_cheia":
        slot = ("media", data["media"])
    elif data.get("tailscale"):
        slot = ("tile", tailscale_tile(data["tailscale"]))

    m, g = 12, 8
    tw = (W - 2 * m - 2 * g) // 3
    th, y0 = (60 if lights else 74), 78
    rows = [items[:3], items[3:6]]
    if slot:
        rows[0 if len(items) <= 2 else 1].append(None)  # None = espaco largo
    rows = [r for r in rows if r]
    for ri, row in enumerate(rows):
        x, y = m, y0 + ri * (th + g)
        for it in row:
            if it is None:
                w = W - m - x
                if slot[0] == "media":
                    mini_player(img, x, y, w, th, slot[1])
                else:
                    tile(d, x, y, w, th, *slot[1])
            else:
                w = tw
                tile(d, x, y, w, th, *it)
            x += w + g

    if lights:
        ly = y0 + len(rows) * (th + g) + 4
        lh = H - ly - 12
        lw = (W - 2 * m - (len(lights) - 1) * g) // len(lights)
        for i, l in enumerate(lights):
            x, on = m + i * (lw + g), l["on"]
            lamp_color = tuple(l["rgb"]) if on and l.get("rgb") else None
            d.rounded_rectangle((x, ly, x + lw, ly + lh), radius=14, fill=YELLOW if on else TILE)
            d.text((x + 12, ly + 10), fit(d, l["name"], font(13), lw - 20), font=font(13),
                   fill=DARK if on else MUTED)
            val = "—" if not l["ok"] else (f"{l['pct']}%" if l["pct"] is not None else "On") \
                if on else "Off"
            d.text((x + 12, ly + lh - 24), val, font=font(26),
                   fill=DARK if on else TXT, anchor="ls")
            bx0, bx1, by = x + 12, x + lw - 12, ly + lh - 12
            bar_bg = (200, 150, 30) if on else (50, 52, 62)
            bar_fg = lamp_color if lamp_color else (DARK if on else (50, 52, 62))
            d.rounded_rectangle((bx0, by - 4, bx1, by), radius=2,
                                fill=bar_bg)
            if on and l["pct"]:
                d.rounded_rectangle((bx0, by - 4, bx0 + (bx1 - bx0) * l["pct"] // 100, by),
                                    radius=2, fill=bar_fg)
    return img


def render_alert(img, alerts):
    """Desenha um aviso compacto sobre a tela, sem alterar o layout de fundo."""
    if not alerts:
        return img
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x0, y0, x1, y1 = 26, 82, W - 26, H - 82
    d.rounded_rectangle((x0 + 3, y0 + 5, x1 + 3, y1 + 5), radius=22, fill=(0, 0, 0, 150))
    d.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=(28, 26, 34, 248), outline=(240, 84, 84, 255), width=3)
    d.rounded_rectangle((x0 + 15, y0 + 15, x0 + 21, y1 - 15), radius=3, fill=(240, 84, 84, 255))
    d.ellipse((x0 + 34, y0 + 17, x0 + 61, y0 + 44), fill=(240, 84, 84, 255))
    d.text((x0 + 47.5, y0 + 17), "!", font=font(20), fill=(255, 255, 255), anchor="mt")
    d.text((x0 + 72, y0 + 18), "ATENÇÃO", font=font(18), fill=(255, 255, 255))
    shown = alerts[:3]
    for i, message in enumerate(shown):
        d.text((x0 + 34, y0 + 58 + i * 26), fit(d, message, font(15, False), x1 - x0 - 52),
               font=font(15, False), fill=(242, 224, 224))
    if len(alerts) > 3:
        d.text((x0 + 34, y1 - 26), f"e mais {len(alerts) - 3} alerta(s)",
               font=font(12, False), fill=(190, 186, 198))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


_bg_cache = {}


def wrap(d, text, f, max_w, max_lines):
    """Quebra por palavras; devolve (linhas, coube_tudo)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= max_w:
            cur = t
            continue
        if cur:
            lines.append(cur)
        cur = w
        while d.textlength(cur, font=f) > max_w and len(cur) > 1:  # palavra gigante
            k = len(cur)
            while k > 1 and d.textlength(cur[:k], font=f) > max_w:
                k -= 1
            lines.append(cur[:k])
            cur = cur[k:]
    if cur:
        lines.append(cur)
    fits = len(lines) <= max_lines
    lines = lines[:max_lines]
    if not fits:
        lines[-1] = fit(d, lines[-1] + "…", f, max_w - 4).rstrip() if d.textlength(
            lines[-1] + "…", font=f) <= max_w else fit(d, lines[-1], f, max_w - 14) + "…"
    return lines, fits


def accent_from(art):
    """Cor de destaque: media da capa, puxada para um tom vivo e claro."""
    if art is None:
        return (30, 215, 96)
    r, g, b = art.resize((1, 1), Image.BOX).getpixel((0, 0))
    mx = max(r, g, b) or 1
    k = 235 / mx
    return tuple(min(255, int(c * k)) for c in (r, g, b))


def player_background(media):
    key = media["key"]
    if key in _bg_cache:
        return _bg_cache[key]
    art = media["art"]
    if art is None:
        bg = Image.new("RGB", (W, H), (22, 24, 30))
    else:
        side = max(W, H)
        big = art.resize((side, side), Image.BILINEAR).crop(((side - W) // 2, (side - H) // 2,
                                                              (side + W) // 2, (side + H) // 2))
        bg = big.filter(ImageFilter.GaussianBlur(28))
        bg = ImageEnhance.Brightness(bg).enhance(0.38)
    if len(_bg_cache) > 8:
        _bg_cache.clear()
    _bg_cache[key] = bg
    return bg


def render_player(media, now=None):
    now = now or datetime.now()
    img = player_background(media).copy()
    d = ImageDraw.Draw(img)
    art, acc = media["art"], accent_from(media["art"])

    # capa com cantos arredondados e sombra
    size, ax, ay, rad = 224, 24, (H - 224) // 2, 20
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((ax + 2, ay + 8, ax + size + 2, ay + size + 8),
                                             radius=rad, fill=(0, 0, 0, 150))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(10))).convert("RGB"))
    if art is not None:
        cover = art.resize((size, size), Image.LANCZOS)
    else:
        cover = Image.new("RGB", (size, size), (40, 44, 54))
        cd = ImageDraw.Draw(cover)
        cd.ellipse((size // 2 - 40, size // 2 - 40, size // 2 + 40, size // 2 + 40), outline=acc, width=6)
        cd.ellipse((size // 2 - 8, size // 2 - 8, size // 2 + 8, size // 2 + 8), fill=acc)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=rad, fill=255)
    img.paste(cover, (ax, ay), mask)
    d = ImageDraw.Draw(img)

    # coluna de texto
    tx, tw = ax + size + 28, W - (ax + size + 28) - 22
    ty = ay + 2
    d.ellipse((tx, ty + 4, tx + 9, ty + 13), fill=acc)
    d.text((tx + 17, ty), "PAUSADO" if media["paused"] else "TOCANDO AGORA",
           font=font(12), fill=(*acc,))
    for size_t in (28, 25, 22, 20):  # maior fonte em que o titulo cabe em 3 linhas
        ft = font(size_t)
        lines, fits = wrap(d, media["title"], ft, tw, 3)
        if fits:
            break
    y = ty + 30
    for ln in lines:
        d.text((tx, y), ln, font=ft, fill=(255, 255, 255))
        y += size_t + 6
    fa = font(18, False)
    for ln in wrap(d, media["artist"], fa, tw, 2)[0]:
        d.text((tx, y + 6), ln, font=fa, fill=(*(int(c * 0.55 + 255 * 0.45) for c in acc),))
        y += 24
    if media["album"] and y < ay + size - 52:
        d.text((tx, y + 10), fit(d, media["album"], font(13, False), tw), font=font(13, False),
               fill=(185, 188, 198))
    d.text((W - 22, ay + size - 2), now.strftime("%H:%M"), font=font(15), fill=(210, 212, 220), anchor="rs")
    return img


def demo_art():
    s, art = 600, Image.new("RGB", (600, 600))
    ad = ImageDraw.Draw(art)
    for i in range(s):
        ad.line((0, i, s, i), fill=(int(60 + 150 * i / s), int(30 + 60 * i / s), int(150 - 70 * i / s)))
    ad.ellipse((120, 120, 480, 480), fill=(250, 190, 80))
    ad.ellipse((230, 230, 370, 370), fill=(60, 30, 110))
    return art


DEMO_MEDIA = {"title": "Nome de uma Música Bem Comprida Demais", "artist": "Nome do Artista",
              "album": "Álbum de Exemplo", "paused": False, "key": "demo", "art": None}

DEMO = {
    "lights": [{"name": "Sala", "on": True, "pct": 39, "ok": True},
               {"name": "Luminária", "on": True, "pct": 80, "ok": True},
               {"name": "Corredor", "on": True, "pct": None, "ok": True},
               {"name": "LED", "on": False, "pct": None, "ok": True}],
    "people": [("Ana", True), ("Bruno", False)], "net": True, "bat": 64, "charging": False,
    "updates": 2, "has_net": True, "has_phone": True, "phone_name": "Cel. Bruno",
    "media": None, "player_mode": "mini",
    "tailscale": {"state": "Running", "ip": "100.64.0.7", "online": 4, "total": 6},
}


# ----------------------------------------------------------------- programa
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--preview", action="store_true", help="gera preview.png e sai")
    ap.add_argument("--config", default=os.path.join(HERE, "ha_screen.ini"))
    args = ap.parse_args()

    global FONT_DIR
    if args.preview:
        # em preview a fonte vem do projeto indicado no .ini, se existir; senao use --config
        try:
            cfg = Config(args.config)
            FONT_DIR = os.path.join(cfg.project, "res", "fonts", "roboto")
        except SystemExit:
            sys.exit("Para o preview, tenha o ha_screen.ini apontando para o projeto da tela (fonte Roboto).")
        now = datetime(2026, 10, 24, 15, 48)
        render(DEMO, now=now).save(os.path.join(HERE, "preview.png"))
        DEMO_MEDIA["art"] = demo_art()
        playing = dict(DEMO, media=dict(DEMO_MEDIA, title="Nome da Música", tailscale=None), tailscale=None)
        render(playing, now=now).save(os.path.join(HERE, "preview_player.png"))
        render_player(DEMO_MEDIA, now=now).save(os.path.join(HERE, "preview_player_cheio.png"))
        print("previews salvos")
        return

    cfg = Config(args.config)
    if not cfg.token or cfg.token.startswith("COLE_"):
        sys.exit("Edite o ha_screen.ini e coloque seu token do Home Assistant.")
    if not os.path.isdir(os.path.join(cfg.project, "library")):
        sys.exit(f"Pasta do projeto da tela invalida: {cfg.project}\n"
                 "Ajuste 'projeto' na secao [tela] do .ini.")
    FONT_DIR = os.path.join(cfg.project, "res", "fonts", "roboto")
    os.chdir(cfg.project)           # a biblioteca usa caminhos relativos
    sys.path.insert(0, cfg.project)

    from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation
    from library.lcd.lcd_comm_rev_b import LcdCommRevB

    cls = {"A": LcdCommRevA, "B": LcdCommRevB}.get(cfg.revision)
    if cls is None:
        sys.exit("revisao deve ser A ou B")
    lcd = cls(com_port=cfg.port, display_width=320, display_height=480)
    lcd.Reset()
    lcd.InitializeComm()
    lcd.SetBrightness(level=cfg.brightness)  # rev. A esquenta: mantenha ate ~50
    lcd.SetOrientation(orientation=Orientation.LANDSCAPE)

    stop = []
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))

    data, online, last, next_fetch = None, True, None, 0
    alert_signature, alert_until, next_reminder = None, 0.0, 0.0
    while not stop:
        if time.time() >= next_fetch:
            try:
                data, online = fetch(cfg), True
            except Exception as e:  # HA/rede fora: mantem o ultimo dado
                print("HA:", e)
                online = False
            playing = bool(data and data.get("media"))
            next_fetch = time.time() + (min(cfg.every, 5) if playing else cfg.every)
        full = data and data.get("media") and data.get("player_mode") == "tela_cheia"
        img = render_player(data["media"]) if full else render(data, online)
        alerts = data.get("alerts", []) if data else []
        now_mono = time.monotonic()
        signature = tuple(alerts)
        if not signature:
            alert_signature, alert_until, next_reminder = None, 0.0, 0.0
        elif signature != alert_signature:
            alert_signature = signature
            alert_until = now_mono + cfg.alert_duration
            next_reminder = now_mono + cfg.alert_repeat
        elif now_mono >= next_reminder:
            alert_until = now_mono + cfg.alert_duration
            next_reminder = now_mono + cfg.alert_repeat
        if alerts and now_mono < alert_until:
            img = render_alert(img, alerts)
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h != last:  # so reenvia se mudou algo (evita piscar)
            lcd.DisplayPILImage(img, 0, 0)
            last = h
        time.sleep(1)
    lcd.closeSerial()


if __name__ == "__main__":
    main()
