#!/usr/bin/env python3
"""Gerenciador gráfico do painel Turing Smart Screen + Home Assistant."""
import configparser
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

APP_NAME = "Turing Smart Screen — Gerenciador"
DEFAULT_PROJECT = r"C:\tela\turing-smart-screen-python-modificado\turing-smart-screen-python-main"


def app_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def bool_yes(value):
    return str(value).strip().lower() in ("sim", "s", "true", "1", "yes", "on")


class Manager:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("900x760")
        root.minsize(780, 650)
        self.proc = None
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.optionxform = str
        self.vars = {}
        self._style()
        self._build()
        self._load_config()
        self.root.after(1000, self._monitor)

    def _style(self):
        self.root.configure(bg="#11131a")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#11131a")
        style.configure("TLabel", background="#11131a", foreground="#e9eaf0", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#a8adbd")
        style.configure("TNotebook", background="#11131a", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#6e5bff", foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#806fff")])
        style.configure("TEntry", fieldbackground="#20232d", foreground="#f6f6fa")
        style.configure("TCombobox", fieldbackground="#20232d", foreground="#f6f6fa")

    def _build(self):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Turing Smart Screen", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Configure a integração, escolha o que aparece e controle a tela sem usar o Prompt de Comando.", style="Sub.TLabel").pack(anchor="w", pady=(2, 12))

        project_row = ttk.Frame(outer)
        project_row.pack(fill="x", pady=(0, 12))
        ttk.Label(project_row, text="Pasta do projeto:").pack(side="left")
        self.project_var = tk.StringVar(value=DEFAULT_PROJECT)
        ttk.Entry(project_row, textvariable=self.project_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(project_row, text="Procurar…", command=self._browse_project).pack(side="left")

        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.tab_conn = ttk.Frame(self.tabs, padding=18)
        self.tab_screen = ttk.Frame(self.tabs, padding=18)
        self.tab_entities = ttk.Frame(self.tabs, padding=18)
        self.tab_advanced = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.tab_conn, text="Home Assistant")
        self.tabs.add(self.tab_screen, text="Tela e alertas")
        self.tabs.add(self.tab_entities, text="O que aparece")
        self.tabs.add(self.tab_advanced, text="Avançado")
        self._build_connection()
        self._build_screen()
        self._build_entities()
        self._build_advanced()

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(14, 4))
        self.status = tk.StringVar(value="Pronto. Configure e salve antes de iniciar.")
        ttk.Label(buttons, textvariable=self.status, style="Sub.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Testar conexão", command=self._test_connection).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Salvar", command=self._save).pack(side="right", padx=(8, 0))
        self.start_btn = ttk.Button(buttons, text="▶ Iniciar tela", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="right", padx=(8, 0))
        self.stop_btn = ttk.Button(buttons, text="■ Parar", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right")
        ttk.Label(outer, text="O token é gravado apenas no ha_screen.ini local. Não compartilhe esse arquivo.", style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

    def _field(self, parent, label, key, default="", secret=False, width=52):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text=label, width=27).pack(side="left", anchor="w")
        var = tk.StringVar(value=default)
        ent = ttk.Entry(row, textvariable=var, width=width, show="•" if secret else "")
        ent.pack(side="left", fill="x", expand=True)
        self.vars[key] = var
        if secret:
            show_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text="Mostrar", variable=show_var, command=lambda e=ent, v=show_var: e.configure(show="" if v.get() else "•")).pack(side="left", padx=7)
        return row

    def _build_connection(self):
        ttk.Label(self.tab_conn, text="Acesso à API do Home Assistant", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        self._field(self.tab_conn, "URL do Home Assistant", "ha.url", "http://homeassistant.local:8123")
        self._field(self.tab_conn, "Token de longa duração", "ha.token", "", secret=True)
        self._field(self.tab_conn, "Intervalo de consulta (segundos)", "ha.atualizar_seg", "2")
        ttk.Label(self.tab_conn, text="No Home Assistant, crie um token em Perfil → Segurança → Tokens de acesso de longa duração. O token nunca é enviado a este programa; o teste usa-o somente para consultar /api/states.", wraplength=760, style="Sub.TLabel").pack(anchor="w", pady=16)

    def _build_screen(self):
        ttk.Label(self.tab_screen, text="Dispositivo e avisos", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        self._field(self.tab_screen, "Porta serial (ex.: COM3)", "tela.porta", "COM3")
        self._field(self.tab_screen, "Revisão do display (A ou B)", "tela.revisao", "A")
        self._field(self.tab_screen, "Brilho (0 a 100)", "tela.brilho", "30")
        ttk.Separator(self.tab_screen).pack(fill="x", pady=14)
        self._field(self.tab_screen, "Aviso fica visível (segundos)", "alertas.duracao_seg", "7")
        self._field(self.tab_screen, "Lembrar novamente (minutos)", "alertas.repetir_min", "30")
        self._field(self.tab_screen, "Bateria crítica (percentual)", "entidades.celular_alerta_bateria", "10")
        self._field(self.tab_screen, "Nome do celular na tela", "entidades.celular_nome", "Celular")
        self._field(self.tab_screen, "Sensor de bateria", "entidades.celular_bateria", "")
        self._field(self.tab_screen, "Sensor de estado/carregamento", "entidades.celular_carregando", "")
        self._field(self.tab_screen, "Sensor de internet (opcional)", "entidades.internet", "")

    def _text_box(self, parent, label, key, example, height=8):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(7, 3))
        box = tk.Text(parent, height=height, wrap="none", bg="#20232d", fg="#f6f6fa", insertbackground="white", relief="flat", font=("Consolas", 10), undo=True)
        box.pack(fill="both", expand=True)
        box.insert("1.0", example)
        self.vars[key] = box
        return box

    def _build_entities(self):
        ttk.Label(self.tab_entities, text="Escolha os dispositivos exibidos", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.tab_entities, text="Uma entidade por linha no formato entity_id = Nome mostrado. Câmeras e luzes/LEDs também geram alerta quando ficam indisponíveis.", style="Sub.TLabel", wraplength=760).pack(anchor="w", pady=(2, 8))
        self._text_box(self.tab_entities, "Luzes e LEDs (até 4 cartões)", "luzes", "light.luz_mesa = Mesa\nlight.luminaria = Luminária\nswitch.luz_corredor_switch_1 = Corredor\nswitch.led_parede_switch_1 = LED parede", 7)
        self._text_box(self.tab_entities, "Câmeras monitoradas", "cameras", "camera.camera_inteligente_com_botao_de_chamada = Campainha\ncamera.ekaza_camera_dome_nuvem = Câmera dome", 5)
        ttk.Label(self.tab_entities, text="Entidades que não quiser mostrar podem ser apagadas da lista. Para ocultar celular ou internet, deixe o campo correspondente vazio na aba Tela e alertas.", style="Sub.TLabel", wraplength=760).pack(anchor="w", pady=8)

    def _build_advanced(self):
        ttk.Label(self.tab_advanced, text="Opções adicionais", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self._field(self.tab_advanced, "Entidade do player (opcional)", "player.entidade", "")
        self._field(self.tab_advanced, "Modo do player (mini/tela_cheia)", "player.modo", "mini")
        row = ttk.Frame(self.tab_advanced); row.pack(fill="x", pady=6)
        self.vars["player.mostrar_pausado"] = tk.StringVar(value="nao")
        ttk.Label(row, text="Mostrar player pausado", width=27).pack(side="left")
        ttk.Combobox(row, textvariable=self.vars["player.mostrar_pausado"], values=("nao", "sim"), state="readonly", width=12).pack(side="left")
        self.vars["tailscale.ativar"] = tk.StringVar(value="sim")
        row = ttk.Frame(self.tab_advanced); row.pack(fill="x", pady=6)
        ttk.Label(row, text="Mostrar informações Tailscale", width=27).pack(side="left")
        ttk.Combobox(row, textvariable=self.vars["tailscale.ativar"], values=("sim", "nao"), state="readonly", width=12).pack(side="left")
        self._field(self.tab_advanced, "Comando Tailscale (opcional)", "tailscale.comando", "")
        ttk.Button(self.tab_advanced, text="Abrir código do painel no Bloco de Notas", command=self._open_code).pack(anchor="w", pady=(16, 4))
        ttk.Label(self.tab_advanced, text="A edição livre do código é para personalizações avançadas. Faça uma cópia de segurança antes de alterar ha_screen.py.", style="Sub.TLabel", wraplength=760).pack(anchor="w")

    def _project(self):
        p = Path(self.project_var.get().strip().strip('"'))
        if not (p / "library").is_dir() or not (p / "res" / "fonts" / "roboto").is_dir():
            raise FileNotFoundError(f"Não encontrei a biblioteca da tela e as fontes em:\n{p}\n\nEscolha a pasta do projeto turing-smart-screen-python.")
        return p

    def _config_path(self):
        return self._project() / "ha_screen.ini"

    def _browse_project(self):
        p = filedialog.askdirectory(title="Selecione a pasta do projeto Turing Smart Screen")
        if p:
            self.project_var.set(p)
            self._load_config()

    def _read_ini(self):
        p = self._config_path()
        c = configparser.ConfigParser(interpolation=None)
        c.optionxform = str
        if p.exists():
            c.read(p, encoding="utf-8")
        return c

    def _set(self, c, dotted, val):
        section, key = dotted.split(".", 1)
        if not c.has_section(section):
            c.add_section(section)
        c.set(section, key, str(val).strip())

    def _get(self, c, dotted, default=""):
        section, key = dotted.split(".", 1)
        return c.get(section, key, fallback=default) if c.has_section(section) else default

    def _load_config(self):
        try:
            c = self._read_ini()
            keys = ["ha.url", "ha.token", "ha.atualizar_seg", "tela.porta", "tela.revisao", "tela.brilho",
                    "alertas.duracao_seg", "alertas.repetir_min", "entidades.celular_alerta_bateria",
                    "entidades.celular_nome", "entidades.celular_bateria", "entidades.celular_carregando",
                    "entidades.internet", "player.entidade", "player.modo", "player.mostrar_pausado",
                    "tailscale.ativar", "tailscale.comando"]
            defaults = {"ha.url":"http://homeassistant.local:8123", "ha.atualizar_seg":"2", "tela.porta":"COM3",
                        "tela.revisao":"A", "tela.brilho":"30", "alertas.duracao_seg":"7", "alertas.repetir_min":"30",
                        "entidades.celular_alerta_bateria":"10", "entidades.celular_nome":"Celular",
                        "entidades.celular_bateria":"", "entidades.celular_carregando":"", "entidades.internet":"",
                        "player.entidade":"", "player.modo":"mini", "player.mostrar_pausado":"nao",
                        "tailscale.ativar":"sim", "tailscale.comando":""}
            for k in keys:
                if k in self.vars:
                    self.vars[k].set(self._get(c, k, defaults.get(k, "")))
            self.vars["luzes"].delete("1.0", "end")
            self.vars["luzes"].insert("1.0", "\n".join(f"{k} = {v}" for k,v in c.items("luzes")) if c.has_section("luzes") else "")
            self.vars["cameras"].delete("1.0", "end")
            self.vars["cameras"].insert("1.0", "\n".join(f"{k} = {v}" for k,v in c.items("cameras")) if c.has_section("cameras") else "")
            self.status.set(f"Configuração carregada: {self._config_path()}")
        except Exception as e:
            self.status.set(str(e))

    def _parse_device_lines(self, widget, title, limit=None):
        result=[]
        for n,line in enumerate(widget.get("1.0","end").splitlines(),1):
            line=line.strip()
            if not line or line.startswith("#"): continue
            if "=" not in line:
                raise ValueError(f"{title}, linha {n}: use entity_id = nome")
            entity,name=(part.strip() for part in line.split("=",1))
            if "." not in entity or not name:
                raise ValueError(f"{title}, linha {n}: entidade/nome inválidos")
            result.append((entity,name))
        if limit is not None and len(result)>limit:
            raise ValueError(f"{title}: máximo {limit} entidades.")
        return result

    def _save(self, quiet=False):
        try:
            project=self._project()
            lights=self._parse_device_lines(self.vars["luzes"],"Luzes",4)
            cameras=self._parse_device_lines(self.vars["cameras"],"Câmeras")
            c=self._read_ini()
            for key,var in self.vars.items():
                if "." in key and isinstance(var, tk.StringVar): self._set(c,key,var.get())
            for sec,pairs in (("luzes",lights),("cameras",cameras)):
                if c.has_section(sec): c.remove_section(sec)
                c.add_section(sec)
                for entity,name in pairs: c.set(sec,entity,name)
            c["tela"]["projeto"]=str(project)
            c["tela"]["porta"]=c["tela"].get("porta","COM3") or "AUTO"
            c["tela"]["revisao"]=c["tela"].get("revisao","A").upper()
            c["alertas"]["duracao_seg"]=str(max(1,int(c["alertas"].get("duracao_seg","7"))))
            c["alertas"]["repetir_min"]=str(max(1,int(c["alertas"].get("repetir_min","30"))))
            c["entidades"]["celular_alerta_bateria"]=str(max(0,min(100,int(c["entidades"].get("celular_alerta_bateria","10")))))
            path=project/"ha_screen.ini"
            tmp=path.with_suffix(".ini.tmp")
            with tmp.open("w",encoding="utf-8") as f: c.write(f)
            tmp.replace(path)
            self.config=c
            self.status.set(f"Salvo: {path}")
            if not quiet: messagebox.showinfo("Configuração salva","As configurações foram gravadas no ha_screen.ini.")
            return True
        except Exception as e:
            self.status.set("Não foi possível salvar")
            if not quiet: messagebox.showerror("Erro ao salvar",str(e))
            return False

    def _test_connection(self):
        if not self._save(quiet=True): return
        url=self.vars["ha.url"].get().strip().rstrip("/")
        token=self.vars["ha.token"].get().strip()
        if not url or not token or "COLE_NOVO_TOKEN_AQUI" in token:
            messagebox.showwarning("Falta configuração","Preencha a URL e o token de longa duração.")
            return
        self.status.set("Testando conexão com o Home Assistant…")
        def work():
            try:
                req=urllib.request.Request(url+"/api/states",headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
                with urllib.request.urlopen(req,timeout=10) as response:
                    import json
                    states=json.loads(response.read().decode("utf-8"))
                    result=f"Conexão OK — API autorizada; {len(states)} entidades recebidas."
                    good=True
            except urllib.error.HTTPError as e:
                result=f"HTTP {e.code}: autenticação recusada. Confira token e URL."
                good=False
            except Exception as e:
                result=f"Falha de conexão: {e}"
                good=False
            self.root.after(0,lambda: (self.status.set(result),messagebox.showinfo("Teste Home Assistant" if good else "Falha no teste",result) if good else messagebox.showerror("Falha no teste",result)))
        threading.Thread(target=work,daemon=True).start()

    def _panel_exe(self):
        candidates=[app_dir()/"TuringPainel.exe"]
        return next((p for p in candidates if p.is_file()),None)

    def _pythonw(self):
        p=shutil.which("pythonw.exe") or shutil.which("pythonw")
        if p: return p
        py=shutil.which("python.exe") or shutil.which("python")
        if py:
            p=Path(py)
            candidate=p.with_name("pythonw.exe")
            if candidate.exists(): return str(candidate)
            return str(p)
        return None

    def _start(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Tela","O painel já está em execução."); return
        if not self._save(quiet=True): return
        project=self._project(); ini=project/"ha_screen.ini"
        token=self.vars["ha.token"].get().strip()
        if not token or "COLE_NOVO_TOKEN_AQUI" in token:
            self.tabs.select(self.tab_conn); messagebox.showwarning("Falta token","Cole o token de longa duração na aba Home Assistant e salve."); return
        binary=self._panel_exe()
        if binary:
            cmd=[str(binary),"--config",str(ini)]
        else:
            py=self._pythonw()
            if not py:
                messagebox.showerror("Python não encontrado","Instale Python 3 para Windows e marque Add Python to PATH. Depois reabra o gerenciador.")
                return
            panel_script=app_dir()/"ha_screen.py"
            if not panel_script.is_file():
                messagebox.showerror("Script do painel não encontrado",f"Não encontrei {panel_script}")
                return
            cmd=[py,str(panel_script),"--config",str(ini)]
        try:
            log=(project/"turing_panel.log").open("a",encoding="utf-8")
            kwargs={"cwd":str(project),"stdout":log,"stderr":subprocess.STDOUT}
            if os.name=="nt": kwargs["creationflags"]=subprocess.CREATE_NO_WINDOW
            self.proc=subprocess.Popen(cmd,**kwargs)
            self.status.set("Painel iniciado. Diagnóstico: turing_panel.log na pasta do projeto.")
            self._refresh_buttons()
        except Exception as e:
            messagebox.showerror("Não foi possível iniciar",str(e))

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill()
        self.proc=None
        self.status.set("Painel parado.")
        self._refresh_buttons()

    def _monitor(self):
        if self.proc and self.proc.poll() is not None:
            code=self.proc.returncode
            self.proc=None
            self.status.set(f"O processo da tela encerrou (código {code}). Consulte turing_panel.log.")
        self._refresh_buttons()
        self.root.after(1000,self._monitor)

    def _refresh_buttons(self):
        running=bool(self.proc and self.proc.poll() is None)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _open_code(self):
        try:
            path=app_dir()/"ha_screen.py"
            if os.name=="nt": os.startfile(str(path))
            else: subprocess.Popen(["xdg-open",str(path)])
        except Exception as e: messagebox.showerror("Erro",str(e))


def main():
    root=tk.Tk()
    Manager(root)
    root.mainloop()

if __name__=="__main__":
    main()
