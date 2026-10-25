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
import ctypes
import hashlib
import io
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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

PHOTO_INTERVAL_SECONDS = 15
NOTICE_INTERVAL_SECONDS = 60 * 60
NOTICE_VISIBLE_SECONDS = 8
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MESES_COMPLETOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
                   "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

FONT_DIR = HERE  # ajustado em load_config(): <projeto>/res/fonts/roboto
_fonts = {}
PHOTO_DIR = os.path.join(HERE, "fotos")
_photo_dir_cache = {"directory": None, "signature": None, "paths": []}
_photo_image_cache = {}
_photo_background_cache = {}
_idle_background_cache = {}
_offline_state = {}


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
        self.every = int(ha.get("atualizar_seg", "10"))
        self.project = os.path.abspath(os.path.join(os.path.dirname(path), tela.get("projeto", ".")))
        self.port = tela.get("porta", "AUTO")
        self.revision = tela.get("revisao", "A").upper()
        self.brightness = int(tela.get("brilho", "30"))
        self.idle_after_seconds = max(0, int(tela.get("inatividade_segundos", "300")))
        controls = cp["controles"] if cp.has_section("controles") else {}
        self.control_light = controls.get("tela", "light.tela_turing").strip()
        self.control_brightness = controls.get("brilho", "input_number.brilho_tela_turing").strip()
        self.control_background = controls.get("fundo", "switch.fundo_fotos_tela").strip()
        self.control_screensaver = controls.get("descanso", "switch.modo_descanso_tela_turing").strip()
        self.notification_id = controls.get("notificacao_id", "input_number.notificacao_tela_turing_id").strip()
        self.notification_title = controls.get("notificacao_titulo", "input_text.notificacao_tela_turing_titulo").strip()
        self.notification_message = controls.get("notificacao_mensagem", "input_text.notificacao_tela_turing_mensagem").strip()
        self.notification_style = controls.get("notificacao_estilo", "input_text.notificacao_tela_turing_estilo").strip()
        self.internet = ent.get("internet", "").strip()
        self.phone_name = ent.get("celular_nome", "Celular")
        self.phone_level = ent.get("celular_bateria", "").strip()
        self.phone_state = ent.get("celular_carregando", "").strip()
        self.cameras = []
        for item in ent.get("cameras", "").split(",")[:4]:
            entity, separator, label = item.strip().partition("=")
            if entity.startswith("camera."):
                label = label.strip() if separator else ""
                self.cameras.append((entity, label or entity.split(".", 1)[1].replace("_", " ").title()))
        self.camera_privacy = {}
        for item in ent.get("camera_privacidade", "").split(","):
            camera, separator, privacy = item.strip().partition("=")
            if separator and camera.strip().startswith("camera.") and privacy.strip().startswith("switch."):
                self.camera_privacy[camera.strip()] = privacy.strip()
        self.lights = [(k.strip(), v.strip()) for k, v in cp["luzes"].items()][:4] \
            if cp.has_section("luzes") else []
        sp = cp["player"] if cp.has_section("player") else {}
        self.player = sp.get("entidade", "").strip()
        self.show_paused = sp.get("mostrar_pausado", "nao").strip().lower() in ("sim", "s", "true", "1", "yes")
        self.player_mode = "tela_cheia" if sp.get("modo", "mini").strip().lower() in (
            "tela_cheia", "cheia", "full") else "mini"
        ts = cp["tailscale"] if cp.has_section("tailscale") else {}
        self.tailscale = ts.get("ativar", "sim").strip().lower() in ("sim", "s", "true", "1", "yes")
        self.tailscale_cmd = ts.get("comando", "").strip()
        # O slideshow usa sempre esta pasta dentro do projeto, sem exigir
        # nenhuma configuracao adicional no arquivo .ini.
        self.photo_dir = os.path.join(self.project, "fotos")


# --------------------------------------------------------------- dados HA
_art_cache = {}  # url -> Image (so baixa a capa quando a musica muda)


def list_photo_paths(directory=PHOTO_DIR):
    """Retorna as imagens da pasta fotos e detecta arquivos novos sem reiniciar."""
    directory = os.path.abspath(directory)
    entries = []
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS:
                try:
                    stat = os.stat(path)
                    entries.append((name.casefold(), stat.st_mtime_ns, stat.st_size, path))
                except OSError:
                    continue
    except OSError:
        entries = []
    entries.sort(key=lambda item: item[0])
    signature = tuple((name, mtime, size) for name, mtime, size, _ in entries)
    if (_photo_dir_cache["directory"] != directory or
            _photo_dir_cache["signature"] != signature):
        _photo_dir_cache["directory"] = directory
        _photo_dir_cache["signature"] = signature
        _photo_dir_cache["paths"] = [path for _, _, _, path in entries]
        # Limpa tambem arquivos modificados no lugar (por exemplo, quando a
        # foto e substituida por uma versao corrigida).
        _photo_image_cache.clear()
    return _photo_dir_cache["paths"]


def load_photo(path):
    """Abre uma foto local uma vez e devolve uma copia RGB pronta para desenhar."""
    if path not in _photo_image_cache:
        try:
            with Image.open(path) as source:
                # Celulares costumam salvar a orientacao no EXIF, sem girar
                # os pixels. Aplica-la evita retratos exibidos de lado.
                _photo_image_cache[path] = ImageOps.exif_transpose(source).convert("RGB")
        except Exception as e:
            print("foto:", e)
            _photo_image_cache[path] = None
    photo = _photo_image_cache[path]
    return photo.copy() if photo is not None else None


def photo_for(directory, now):
    """Escolhe a foto atual de forma deterministica durante cada intervalo."""
    paths = list_photo_paths(directory)
    if not paths:
        return None
    path = paths[int(now.timestamp() // PHOTO_INTERVAL_SECONDS) % len(paths)]
    return load_photo(path)


def photo_background(directory, now):
    """Cria um papel de parede desfocado e escurecido para toda a tela."""
    paths = list_photo_paths(directory)
    if not paths:
        return Image.new("RGB", (W, H), BG)
    path = paths[int(now.timestamp() // PHOTO_INTERVAL_SECONDS) % len(paths)]
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return Image.new("RGB", (W, H), BG)
    if key not in _photo_background_cache:
        photo = load_photo(path)
        if photo is None:
            return Image.new("RGB", (W, H), BG)
        background = ImageOps.fit(
            photo, (W, H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
        background = background.filter(ImageFilter.GaussianBlur(radius=22))
        background = ImageEnhance.Color(background).enhance(0.58)
        background = ImageEnhance.Brightness(background).enhance(0.50)
        if len(_photo_background_cache) >= 8:
            _photo_background_cache.clear()
        _photo_background_cache[key] = background
    return _photo_background_cache[key].copy()


def screensaver_background(directory, now):
    """Foto nítida em tela cheia para o modo descanso."""
    paths = list_photo_paths(directory)
    if not paths:
        return Image.new("RGB", (W, H), BG)
    path = paths[int(now.timestamp() // PHOTO_INTERVAL_SECONDS) % len(paths)]
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return Image.new("RGB", (W, H), BG)
    if key not in _idle_background_cache:
        photo = load_photo(path)
        if photo is None:
            return Image.new("RGB", (W, H), BG)
        background = ImageOps.fit(
            photo, (W, H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
        background = ImageEnhance.Color(background).enhance(0.92)
        background = ImageEnhance.Brightness(background).enhance(0.82)
        background = Image.blend(background, Image.new("RGB", (W, H), (5, 7, 12)), 0.08)
        if len(_idle_background_cache) >= 8:
            _idle_background_cache.clear()
        _idle_background_cache[key] = background
    return _idle_background_cache[key].copy()


def _clamped_rgb(values):
    return tuple(max(0, min(255, int(round(float(value))))) for value in values[:3])


def light_color(attributes):
    """Extrai a cor efetiva da luz no formato RGB usado pelo desenho."""
    rgb = attributes.get("rgb_color")
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        try:
            return _clamped_rgb(rgb)
        except (TypeError, ValueError):
            pass

    hs = attributes.get("hs_color")
    if isinstance(hs, (list, tuple)) and len(hs) >= 2:
        try:
            h, saturation = float(hs[0]) % 360, max(0, min(100, float(hs[1]))) / 100
            return _clamped_rgb(tuple(channel * 255 for channel in colorsys.hsv_to_rgb(h / 360, saturation, 1)))
        except (TypeError, ValueError):
            pass

    # Luzes brancas que informam temperatura de cor continuam mostrando
    # uma tonalidade coerente, mesmo sem rgb_color.
    try:
        kelvin = float(attributes.get("color_temp_kelvin") or 0)
        if not kelvin:
            mired = float(attributes.get("color_temp") or 0)
            kelvin = 1000000 / mired if mired else 0
        if kelvin:
            temperature = max(10, min(400, kelvin / 100))
            red = 255 if temperature <= 66 else 329.698727446 * ((temperature - 60) ** -0.1332047592)
            green = (99.4708025861 * math.log(temperature) - 161.1195681661
                     if temperature <= 66 else 288.1221695283 * ((temperature - 60) ** -0.0755148492))
            blue = 255 if temperature >= 66 else (0 if temperature <= 19 else 138.5177312231 * math.log(temperature - 10) - 305.0447927307)
            return _clamped_rgb((red, green, blue))
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return YELLOW


def dim_color(color, factor=0.35):
    return tuple(max(0, min(255, int(channel * factor))) for channel in color)


def rounded_panel(img, box, color, radius=12, alpha=205):
    """Pinta um painel escuro translúcido sem ocultar totalmente a foto de fundo."""
    x0, y0, x1, y1 = (int(value) for value in box)
    width, height = x1 - x0 + 1, y1 - y0 + 1
    if width <= 0 or height <= 0:
        return
    panel = Image.new("RGBA", (width, height), (*color, alpha))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    base = img.crop((x0, y0, x1 + 1, y1 + 1)).convert("RGBA")
    composed = Image.alpha_composite(base, panel).convert("RGB")
    img.paste(composed, (x0, y0), mask)


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

    lights = []
    for eid, name in cfg.lights:
        s = st.get(eid)
        state_value = s.get("state", "unavailable") if s else "unavailable"
        on = state_value == "on"
        attributes = (s or {}).get("attributes") or {}
        pct = None
        if on and attributes.get("brightness") is not None:
            pct = round(attributes["brightness"] / 2.55)
        lights.append({"name": name, "entity_id": eid, "on": on, "pct": pct,
                       "color": light_color(attributes) if on else OFF_DOT,
                       "ok": bool(s) and state_value not in ("unavailable", "unknown")})

    people = sorted((s["attributes"].get("friendly_name", eid.split(".", 1)[1]).split()[0],
                     s["state"] == "home")
                    for eid, s in st.items() if eid.startswith("person."))

    try:
        level = int(float(state(cfg.phone_level)))
    except ValueError:
        level = None

    net = {"on": True, "off": False}.get(state(cfg.internet))

    # Monitora somente entidades que o usuario configurou. Assim um sensor
    # temporariamente indisponivel gera um aviso util, sem poluir a tela com
    # entidades internas do Home Assistant.
    monitored = {eid: name for eid, name in cfg.lights}
    monitored.update(dict(cfg.cameras))
    if cfg.internet:
        monitored.setdefault(cfg.internet, "Internet")
    if cfg.phone_level:
        monitored.setdefault(cfg.phone_level, cfg.phone_name)
    if cfg.player:
        monitored.setdefault(cfg.player, "Media player")
    newly_offline = []
    checked_at = time.time()
    for eid, name in monitored.items():
        current = st.get(eid, {}).get("state", "unavailable")
        is_offline = current in ("unavailable", "unknown")
        privacy_entity = cfg.camera_privacy.get(eid)
        if privacy_entity and st.get(privacy_entity, {}).get("state") == "on":
            # Privacidade ligada pode indisponibilizar a imagem por projeto;
            # nesse caso nao e uma falha real a ser notificada.
            is_offline = False
        previous = _offline_state.get(eid)
        if is_offline:
            last_notice = previous.get("last_notice", 0) if previous else 0
            if previous is None or not previous.get("offline") or checked_at - last_notice >= NOTICE_INTERVAL_SECONDS:
                newly_offline.append(name)
                last_notice = checked_at
            _offline_state[eid] = {"offline": True, "last_notice": last_notice}
        else:
            # Se voltar e cair novamente, o proximo aviso pode aparecer na
            # hora, sem esperar o fim do intervalo da queda anterior.
            _offline_state[eid] = {"offline": False, "last_notice": 0}
    offline_notice = None
    if newly_offline:
        offline_notice = {
            "devices": newly_offline,
            "until": checked_at + NOTICE_VISIBLE_SECONDS,
        }

    media = None
    s = st.get(cfg.player) if cfg.player else None
    if s and (s["state"] == "playing" or (cfg.show_paused and s["state"] == "paused")):
        a = s["attributes"]
        media = {"title": a.get("media_title") or a.get("app_name") or "—",
                 "artist": a.get("media_artist") or "", "album": a.get("media_album_name") or "",
                 "paused": s["state"] == "paused", "key": a.get("entity_picture") or "",
                 "art": get_art(cfg, a.get("entity_picture"))}
    light_state = state(cfg.control_light)
    screen_on = light_state == "on" if light_state in ("on", "off") else None
    try:
        brightness = max(1, min(100, int(round(float(state(cfg.control_brightness))))))
    except (TypeError, ValueError):
        brightness = None
    wallpaper_state = state(cfg.control_background)
    background_enabled = (wallpaper_state == "on"
                          if wallpaper_state in ("on", "off") else None)
    screensaver_state = state(cfg.control_screensaver)
    manual_screensaver = (screensaver_state == "on"
                          if screensaver_state in ("on", "off") else None)
    try:
        notification_id = int(float(state(cfg.notification_id)))
    except (TypeError, ValueError):
        notification_id = None
    notification_title = state(cfg.notification_title)
    notification_message = state(cfg.notification_message)
    try:
        notification_style = json.loads(state(cfg.notification_style))
        if not isinstance(notification_style, dict):
            notification_style = {}
    except (TypeError, ValueError):
        notification_style = {}
    if notification_title in ("unknown", "unavailable"):
        notification_title = "Notificação"
    if notification_message in ("unknown", "unavailable"):
        notification_message = ""
    return {
        "media": media,
        "lights": lights, "people": people, "net": net, "bat": level,
        "charging": state(cfg.phone_state) == "charging",
        "updates": sum(1 for e, s in st.items() if e.startswith("update.") and s["state"] == "on"),
        "tailscale": tailscale_info(cfg.tailscale_cmd) if (cfg.tailscale and not media) else None,
        "player_mode": cfg.player_mode,
        "photo_dir": cfg.photo_dir,
        "controls": {"screen_on": screen_on, "brightness": brightness,
                     "background_enabled": background_enabled,
                     "manual_screensaver": manual_screensaver},
        "panel_notification": {"id": notification_id, "title": notification_title,
                               "message": notification_message,
                               "style": notification_style},
        "offline_notice": offline_notice,
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
    rounded_panel(img, (x, y, x + w, y + h), TILE, radius=12, alpha=185)
    d = ImageDraw.Draw(img)
    acc = accent_from(media["art"])
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


def draw_offline_notice(d, names):
    """Aviso curto e integrado ao visual do painel, sem janela do Windows."""
    x, y, w, h = 236, 4, W - 248, 44
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(42, 30, 36), outline=(100, 54, 63))
    d.rounded_rectangle((x + 8, y + 8, x + 11, y + h - 8), radius=2, fill=RED)
    d.text((x + 18, y + 7), "DISPOSITIVO OFFLINE", font=font(9), fill=(255, 145, 145))
    label = ", ".join(names)
    d.text((x + 18, y + 23), ellipsize(d, label, font(12), w - 28), font=font(12), fill=TXT)


def tile(img, x, y, w, h, label, value, color):
    rounded_panel(img, (x, y, x + w, y + h), TILE, radius=12, alpha=185)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x + 8, y + 12, x + 11, y + h - 12), radius=2, fill=color)
    d.text((x + 20, y + 9), fit(d, label, font(12, False), w - 28), font=font(12, False), fill=MUTED)
    for size in (22, 20, 18, 16):  # encolhe a fonte ate caber
        f = font(size)
        if d.textlength(value, font=f) <= w - 28:
            break
    d.text((x + 20, y + h - 12), value, font=f, fill=TXT, anchor="ls")


def render_screensaver(photo_dir, now=None, background_enabled=True):
    """Mostra relógio e data sobre a foto nítida, sem os cards do dashboard."""
    now = now or datetime.now()
    img = (screensaver_background(photo_dir, now) if background_enabled
           else Image.new("RGB", (W, H), BG))
    d = ImageDraw.Draw(img)
    d.text((W // 2, H // 2 - 18), now.strftime("%H:%M"), font=font(68), fill=(255, 255, 255),
           anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
    date_text = (f"{DIAS[now.weekday()]}, {now.day} de "
                 f"{MESES_COMPLETOS[now.month - 1]} de {now.year}")
    d.text((W // 2, H // 2 + 48), date_text, font=font(16, False), fill=(255, 255, 255),
           anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
    return img


def render(data, online=True, now=None, background_enabled=True):
    now = now or datetime.now()
    photo_dir = data.get("photo_dir", PHOTO_DIR) if data else PHOTO_DIR
    img = photo_background(photo_dir, now) if background_enabled else Image.new("RGB", (W, H), BG)
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
                    tile(img, x, y, w, th, *slot[1])
            else:
                w = tw
                tile(img, x, y, w, th, *it)
            x += w + g

    if lights:
        ly = y0 + len(rows) * (th + g) + 4
        lh = H - ly - 12
        lw = (W - 2 * m - (len(lights) - 1) * g) // len(lights)
        for i, l in enumerate(lights):
            x, on = m + i * (lw + g), l["on"]
            if on:
                d.rounded_rectangle((x, ly, x + lw, ly + lh), radius=14, fill=YELLOW)
            else:
                rounded_panel(img, (x, ly, x + lw, ly + lh), TILE, radius=14, alpha=185)
                d = ImageDraw.Draw(img)
            d.text((x + 12, ly + 10), fit(d, l["name"], font(13), lw - 20), font=font(13),
                   fill=DARK if on else MUTED)
            val = "—" if not l["ok"] else (f"{l['pct']}%" if l["pct"] is not None else "On") \
                if on else "Off"
            d.text((x + 12, ly + lh - 24), val, font=font(26), fill=DARK if on else TXT, anchor="ls")
            bx0, bx1, by = x + 12, x + lw - 12, ly + lh - 12
            bar_color = tuple(l.get("color") or YELLOW) if on else (50, 52, 62)
            d.rounded_rectangle((bx0, by - 4, bx1, by), radius=2,
                                fill=dim_color(bar_color) if on else bar_color)
            if on:
                fill_pct = 100 if l["pct"] is None else max(0, min(100, l["pct"]))
                fill_x = bx0 + max(2, (bx1 - bx0) * fill_pct // 100)
                d.rounded_rectangle((bx0, by - 4, min(bx1, fill_x), by), radius=2, fill=bar_color)
    notice = data.get("offline_notice")
    if notice and notice.get("until", 0) > time.time():
        draw_offline_notice(d, notice.get("devices", []))
    return img


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


def _limited_int(value, default, minimum, maximum):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _notification_rgb(value, fallback):
    try:
        return tuple(ImageColor.getrgb(str(value).strip())[:3])
    except (TypeError, ValueError):
        return fallback


def normalize_notification_style(style):
    """Valida entradas enviadas pelo script do HA antes de desenhar."""
    style = style if isinstance(style, dict) else {}
    font_style = str(style.get("font", "regular")).strip().lower()
    if font_style not in ("regular", "bold"):
        font_style = "regular"
    position = str(style.get("position", "bottom")).strip().lower()
    if position not in ("top", "bottom"):
        position = "bottom"
    return {
        "color": _notification_rgb(style.get("color", "#B07CF5"), PURPLE),
        "background_color": _notification_rgb(style.get("background_color", "#161820"), (22, 24, 32)),
        "title_color": _notification_rgb(style.get("title_color", "#F0F1F5"), TXT),
        "message_color": _notification_rgb(style.get("message_color", "#DCDEE6"), (220, 222, 230)),
        "font": font_style,
        "title_size": _limited_int(style.get("title_size", 14), 14, 10, 24),
        "message_size": _limited_int(style.get("message_size", 12), 12, 9, 20),
        "duration": _limited_int(style.get("duration", NOTICE_VISIBLE_SECONDS), NOTICE_VISIBLE_SECONDS, 1, 60),
        "position": position,
        "opacity": _limited_int(style.get("opacity", 92), 92, 20, 100),
    }


def draw_panel_notification(img, notification):
    """Desenha um aviso móvel com cores e dimensões selecionáveis pelo HA."""
    style = notification["style"]
    x, h, w = 12, 100, W - 24
    y = 12 if style["position"] == "top" else H - 112
    rounded_panel(img, (x, y, x + w, y + h), style["background_color"], radius=13,
                  alpha=round(style["opacity"] * 255 / 100))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + w, y + h), radius=13, outline=style["color"], width=1)
    d.rounded_rectangle((x + 10, y + 13, x + 13, y + h - 13), radius=2, fill=style["color"])
    d.text((x + 22, y + 7), "NOTIFICAÇÃO", font=font(9), fill=style["color"])
    bold = style["font"] == "bold"
    title_font = font(style["title_size"], bold)
    message_font = font(style["message_size"], bold)
    d.text((x + 22, y + 19), ellipsize(d, notification["title"], title_font, w - 38),
           font=title_font, fill=style["title_color"])
    lines, _ = wrap(d, notification["message"], message_font, w - 38, 2)
    line_height = style["message_size"] + 3
    for index, line in enumerate(lines):
        d.text((x + 22, y + 50 + index * line_height), line, font=message_font,
               fill=style["message_color"])


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


def system_idle_seconds():
    """Segundos desde a ultima entrada de teclado/mouse na sessao do Windows."""
    if os.name != "nt":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
        return ((tick - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0
    except Exception as e:
        print("inatividade:", e)
        return 0.0


def create_power_event_monitor(lcd, brightness, panel_state=None):
    """Recebe WM_POWERBROADCAST sem criar uma janela visivel no Windows."""
    panel_state = panel_state or {"screen_on": True, "brightness": brightness}
    monitor = {"suspended": False, "image": None, "pump": lambda: None, "close": lambda: None}
    if os.name != "nt":
        return monitor
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:
        print("energia: pywin32 nao esta instalado; a tela nao seguira a suspensao automaticamente")
        return monitor

    suspend_event = getattr(win32con, "PBT_APMSUSPEND", 4)
    resume_events = {
        getattr(win32con, "PBT_APMRESUMEAUTOMATIC", 18),
        getattr(win32con, "PBT_APMRESUMESUSPEND", 7),
        getattr(win32con, "PBT_APMRESUMECRITICAL", 6),
    }

    def redraw_after_resume():
        try:
            if panel_state.get("screen_on", True):
                lcd.ScreenOn()
                lcd.SetBrightness(level=panel_state.get("brightness", brightness))
            else:
                lcd.ScreenOff()
            if panel_state.get("screen_on", True) and monitor["image"] is not None:
                lcd.DisplayPILImage(monitor["image"], 0, 0)
        except Exception as e:
            print("energia:", e)

    def on_window_event(_hwnd, message, wparam, _lparam):
        if message != win32con.WM_POWERBROADCAST:
            return 0
        if wparam == suspend_event:
            if not monitor["suspended"]:
                try:
                    lcd.ScreenOff()
                except Exception as e:
                    print("energia:", e)
                monitor["suspended"] = True
        elif wparam in resume_events:
            if monitor["suspended"]:
                monitor["suspended"] = False
                redraw_after_resume()
        return 1

    class_name = f"haScreenPowerEvents_{os.getpid()}"
    hinst = win32api.GetModuleHandle(None)
    wndclass = win32gui.WNDCLASS()
    wndclass.hInstance = hinst
    wndclass.lpszClassName = class_name
    wndclass.lpfnWndProc = {win32con.WM_POWERBROADCAST: on_window_event}
    try:
        registered = win32gui.RegisterClass(wndclass)
        hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_LEFT,
            registered,
            class_name,
            0,
            0,
            0,
            win32con.CW_USEDEFAULT,
            win32con.CW_USEDEFAULT,
            0,
            0,
            hinst,
            None,
        )
    except Exception as e:
        print("energia: nao foi possivel registrar eventos de suspensao:", e)
        return monitor

    monitor["pump"] = win32gui.PumpWaitingMessages

    def close():
        try:
            win32gui.DestroyWindow(hwnd)
            win32gui.UnregisterClass(class_name, hinst)
        except Exception:
            pass

    monitor["close"] = close
    return monitor


# ----------------------------------------------------------------- programa
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--preview", action="store_true", help="gera previews do painel e sai")
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
        render_screensaver(cfg.photo_dir, now=now).save(os.path.join(HERE, "preview_idle.png"))
        DEMO_MEDIA["art"] = demo_art()
        playing = dict(DEMO, media=dict(DEMO_MEDIA, title="Nome da Música", tailscale=None), tailscale=None)
        render(playing, now=now).save(os.path.join(HERE, "preview_player.png"))
        render_player(DEMO_MEDIA, now=now).save(os.path.join(HERE, "preview_player_cheio.png"))
        print("previews salvos")
        return

    cfg = Config(args.config)
    if "COLE_SEU_TOKEN" in cfg.token:
        sys.exit("Edite o ha_screen.ini e coloque seu token do Home Assistant.")
    if not os.path.isdir(os.path.join(cfg.project, "library")):
        sys.exit(f"Pasta do projeto da tela invalida: {cfg.project}\n"
                 "Ajuste 'projeto' na secao [tela] do .ini.")
    FONT_DIR = os.path.join(cfg.project, "res", "fonts", "roboto")
    os.makedirs(cfg.photo_dir, exist_ok=True)
    global PHOTO_DIR
    PHOTO_DIR = cfg.photo_dir
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
    panel_state = {"screen_on": True, "brightness": cfg.brightness}
    power_monitor = create_power_event_monitor(lcd, cfg.brightness, panel_state)

    stop = []
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))

    data, online, last, next_fetch = None, True, None, 0
    last_notification_id, active_notification = None, None
    while not stop:
        power_monitor["pump"]()
        if power_monitor["suspended"]:
            time.sleep(1)
            continue
        if time.time() >= next_fetch:
            try:
                data, online = fetch(cfg), True
            except Exception as e:  # HA/rede fora: mantem o ultimo dado
                print("HA:", e)
                online = False
            playing = bool(data and data.get("media"))
            next_fetch = time.time() + (min(cfg.every, 5) if playing else cfg.every)
        panel_notification = data.get("panel_notification", {}) if data else {}
        notification_id = panel_notification.get("id")
        if notification_id is not None:
            if last_notification_id is None:
                # Nao reapresenta uma mensagem antiga ao iniciar o programa.
                last_notification_id = notification_id
            elif notification_id != last_notification_id:
                last_notification_id = notification_id
                title = panel_notification.get("title") or "Notificação"
                message = panel_notification.get("message") or ""
                if message:
                    notification_style = normalize_notification_style(
                        panel_notification.get("style", {}))
                    active_notification = {
                        "title": title,
                        "message": message,
                        "style": notification_style,
                        "until": time.time() + notification_style["duration"],
                    }
        controls = data.get("controls", {}) if data else {}
        requested_brightness = controls.get("brightness")
        if requested_brightness is not None and requested_brightness != panel_state["brightness"]:
            try:
                lcd.SetBrightness(level=requested_brightness)
                panel_state["brightness"] = requested_brightness
            except Exception as e:
                print("brilho da tela:", e)
        requested_power = controls.get("screen_on")
        if requested_power is False:
            if panel_state["screen_on"]:
                try:
                    lcd.ScreenOff()
                    panel_state["screen_on"] = False
                    last = None
                except Exception as e:
                    print("desligar tela:", e)
            power_monitor["image"] = None
            time.sleep(1)
            continue
        elif requested_power is True and not panel_state["screen_on"]:
            try:
                lcd.ScreenOn()
                lcd.SetBrightness(level=panel_state["brightness"])
                panel_state["screen_on"] = True
                last = None
            except Exception as e:
                print("ligar tela:", e)
                time.sleep(1)
                continue
        background_enabled = controls.get("background_enabled") is not False
        manual_screensaver = controls.get("manual_screensaver") is True
        idle = (manual_screensaver or
                (cfg.idle_after_seconds > 0 and system_idle_seconds() >= cfg.idle_after_seconds))
        full = data and data.get("media") and data.get("player_mode") == "tela_cheia"
        if idle:
            img = render_screensaver(cfg.photo_dir, background_enabled=background_enabled)
        else:
            img = (render_player(data["media"]) if full else
                   render(data, online, background_enabled=background_enabled))
        if active_notification:
            if active_notification["until"] > time.time():
                draw_panel_notification(img, active_notification)
            else:
                active_notification = None
        power_monitor["image"] = img
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h != last:  # so reenvia se mudou algo (evita piscar)
            lcd.DisplayPILImage(img, 0, 0)
            last = h
        time.sleep(1)
    power_monitor["close"]()
    lcd.closeSerial()


if __name__ == "__main__":
    main()
