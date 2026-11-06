"""Interface graphique (Tkinter, incluse avec Python sous Windows et macOS)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import (
    DEFAULT_TARGET_LRA, DEFAULT_TARGET_TP, PRESETS, VIDEO_EXTENSIONS,
    NormalizeError, Settings, check_ffmpeg, find_videos, plan_outputs,
    normalize,
)

try:  # Glisser-déposer (optionnel) : pip install tkinterdnd2
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_MIXINS: tuple = (TkinterDnD.DnDWrapper,)
except ImportError:  # pragma: no cover - dépend de l'installation
    TkinterDnD = None
    DND_FILES = None
    _DND_MIXINS = ()

PRESET_LABELS = {
    "YouTube / réseaux sociaux (-14 LUFS)": PRESETS["youtube"],
    "Web / podcast (-16 LUFS)": PRESETS["web"],
    "Télévision EBU R128 (-23 LUFS)": PRESETS["broadcast"],
    "Personnalisé": None,
}


def _enable_dnd(root: tk.Tk) -> bool:
    """Active tkdnd sur la fenêtre. Retourne False si indisponible."""
    if TkinterDnD is None:
        return False
    require = getattr(TkinterDnD, "require", None) or TkinterDnD._require
    try:
        require(root)
        return True
    except (RuntimeError, tk.TclError):
        return False


class App(tk.Tk, *_DND_MIXINS):
    def __init__(self, initial_paths: list[str] | None = None) -> None:
        super().__init__()
        self.dnd = _enable_dnd(self)
        self.title("Normaliseur de volume vidéo")
        self.geometry("820x600")
        self.minsize(640, 480)

        self.files: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_requested = threading.Event()

        self._build()
        if initial_paths:
            self._add(initial_paths)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        style = ttk.Style(self)
        style.configure("Drop.Treeview", background="#dbeafe", fieldbackground="#dbeafe")

        # Liste des fichiers
        title = ("Vidéos à normaliser — glissez-déposez vos fichiers ou dossiers ici"
                 if self.dnd else "Vidéos à normaliser")
        frm_files = ttk.LabelFrame(self, text=title)
        frm_files.pack(fill="both", expand=True, **pad)
        self.frm_files = frm_files

        # Les boutons sont placés en premier pour ne jamais être rognés
        btns = ttk.Frame(frm_files)
        btns.pack(side="right", fill="y", padx=4, pady=4)
        ttk.Button(btns, text="Ajouter des vidéos…", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Ajouter un dossier…", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="Retirer la sélection", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Tout vider", command=self.clear).pack(fill="x", pady=2)

        cols = ("fichier", "dossier", "avant", "apres", "statut")
        self.tree = ttk.Treeview(frm_files, columns=cols, show="headings", height=10)
        for c, label, w, stretch in (("fichier", "Fichier", 220, True),
                                     ("dossier", "Dossier", 140, True),
                                     ("avant", "Avant", 90, False),
                                     ("apres", "Après", 90, False),
                                     ("statut", "Statut", 80, False)):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, stretch=stretch,
                             anchor="w" if c in ("fichier", "dossier") else "center")
        sb = ttk.Scrollbar(frm_files, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=4)
        self.tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

        # Message affiché quand la liste est vide
        hint = ("⬇  Glissez-déposez vos vidéos ici  ⬇\n\n(ou utilisez « Ajouter des vidéos… »)"
                if self.dnd else
                "Utilisez « Ajouter des vidéos… » ou « Ajouter un dossier… »\n\n"
                "(pour le glisser-déposer : pip install tkinterdnd2)")
        self.hint = tk.Label(self.tree, text=hint, justify="center",
                             fg="gray45", bg="white", font=("TkDefaultFont", 12))
        self._update_hint()

        if self.dnd:
            # On accepte le dépôt sur toute la fenêtre
            for w in (self, frm_files, self.tree, self.hint):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                w.dnd_bind("<<Drop>>", self._on_drop)


        # Réglages
        frm_set = ttk.LabelFrame(self, text="Réglages")
        frm_set.pack(fill="x", **pad)

        ttk.Label(frm_set, text="Niveau cible :").grid(row=0, column=0, sticky="w", **pad)
        self.preset = tk.StringVar(value="Web / podcast (-16 LUFS)")
        cb = ttk.Combobox(frm_set, textvariable=self.preset, state="readonly",
                          values=list(PRESET_LABELS), width=36)
        cb.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        cb.bind("<<ComboboxSelected>>", self._on_preset)

        ttk.Label(frm_set, text="LUFS :").grid(row=0, column=3, sticky="e", **pad)
        self.lufs = tk.DoubleVar(value=-16.0)
        self.spin_lufs = ttk.Spinbox(frm_set, from_=-40, to=-5, increment=0.5,
                                     textvariable=self.lufs, width=7, state="disabled")
        self.spin_lufs.grid(row=0, column=4, sticky="w", **pad)

        ttk.Label(frm_set, text="Pic max (dBTP) :").grid(row=1, column=0, sticky="w", **pad)
        self.tp = tk.DoubleVar(value=DEFAULT_TARGET_TP)
        ttk.Spinbox(frm_set, from_=-9, to=0, increment=0.5, textvariable=self.tp,
                    width=7).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frm_set, text="LRA (LU) :").grid(row=1, column=3, sticky="e", **pad)
        self.lra = tk.DoubleVar(value=DEFAULT_TARGET_LRA)
        ttk.Spinbox(frm_set, from_=1, to=50, increment=1, textvariable=self.lra,
                    width=7).grid(row=1, column=4, sticky="w", **pad)

        ttk.Label(frm_set, text="Dossier de sortie :").grid(row=2, column=0, sticky="w", **pad)
        self.outdir = tk.StringVar(value="")
        ttk.Entry(frm_set, textvariable=self.outdir, width=50).grid(
            row=2, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(frm_set, text="Parcourir…", command=self.choose_outdir).grid(
            row=2, column=4, sticky="w", **pad)
        ttk.Label(frm_set, foreground="gray",
                  text="Vide = à côté de l'original, avec le suffixe « _normalise »").grid(
            row=3, column=1, columnspan=4, sticky="w", padx=8)

        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_set, text="Écraser les fichiers existants",
                        variable=self.overwrite).grid(row=4, column=1, columnspan=3,
                                                      sticky="w", **pad)
        frm_set.columnconfigure(2, weight=1)

        # Progression + actions
        frm_run = ttk.Frame(self)
        frm_run.pack(fill="x", **pad)
        self.status = tk.StringVar(value="Ajoutez des vidéos pour commencer.")
        ttk.Label(frm_run, textvariable=self.status).pack(fill="x")
        self.pbar_file = ttk.Progressbar(frm_run, maximum=1.0)
        self.pbar_file.pack(fill="x", pady=2)
        self.pbar_all = ttk.Progressbar(frm_run, maximum=1.0)
        self.pbar_all.pack(fill="x", pady=2)

        frm_btn = ttk.Frame(self)
        frm_btn.pack(fill="x", **pad)
        self.btn_stop = ttk.Button(frm_btn, text="Arrêter", command=self.stop,
                                   state="disabled")
        self.btn_stop.pack(side="right", padx=4)
        self.btn_start = ttk.Button(frm_btn, text="Normaliser", command=self.start)
        self.btn_start.pack(side="right", padx=4)

    # ------------------------------------------------------------ actions
    def _on_preset(self, _event=None) -> None:
        value = PRESET_LABELS[self.preset.get()]
        if value is None:
            self.spin_lufs.configure(state="normal")
        else:
            self.lufs.set(value)
            self.spin_lufs.configure(state="disabled")

    # ------------------------------------------------------ glisser-déposer
    def _on_drop_enter(self, event):
        self.hint.configure(bg="#dbeafe")
        self.tree.configure(style="Drop.Treeview")
        return event.action

    def _on_drop_leave(self, event):
        self.hint.configure(bg="white")
        self.tree.configure(style="Treeview")
        return event.action

    def _on_drop(self, event):
        self._on_drop_leave(event)
        # event.data est une liste Tcl : les chemins avec espaces sont entre {}
        self._add(self.tk.splitlist(event.data), only_videos=True)
        return event.action

    def _insert_row(self, i: int, f: Path) -> None:
        self.tree.insert("", "end", iid=str(i),
                         values=(f.name, str(f.parent), "", "", "En attente"))

    def _update_hint(self) -> None:
        if self.files:
            self.hint.place_forget()
        else:
            self.hint.place(relx=0.5, rely=0.55, anchor="center")

    def _add(self, paths, only_videos: bool = False) -> None:
        """Ajoute des fichiers/dossiers. ``only_videos`` ignore les fichiers
        qui ne sont pas des vidéos (utile pour un dépôt en vrac)."""
        paths = [p for p in paths if p]
        rejected = 0
        if only_videos:
            kept = []
            for p in paths:
                pp = Path(p)
                if pp.is_dir() or pp.suffix.lower() in VIDEO_EXTENSIONS:
                    kept.append(p)
                else:
                    rejected += 1
            paths = kept
        try:
            new = find_videos(paths, recursive=True) if paths else []
        except NormalizeError as exc:
            messagebox.showerror("Erreur", str(exc))
            return
        known = {f.resolve() for f in self.files}
        added = 0
        for f in new:
            if f.resolve() not in known:
                self.files.append(f)
                known.add(f.resolve())
                added += 1
                self._insert_row(len(self.files) - 1, f)
        self._update_hint()
        msg = f"{added} vidéo(s) ajoutée(s) — {len(self.files)} dans la liste."
        if rejected:
            msg += f" {rejected} fichier(s) ignoré(s) (pas une vidéo)."
        self.status.set(msg)

    def add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Choisir des vidéos",
            filetypes=[("Vidéos", exts), ("Tous les fichiers", "*.*")])
        if paths:
            self._add(paths)

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Choisir un dossier")
        if d:
            self._add([d])

    def remove_selected(self) -> None:
        if self._busy():
            return
        sel = {int(i) for i in self.tree.selection()}
        self.files = [f for i, f in enumerate(self.files) if i not in sel]
        self._refresh_tree()

    def clear(self) -> None:
        if self._busy():
            return
        self.files = []
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.files):
            self._insert_row(i, f)
        self._update_hint()
        self.status.set(f"{len(self.files)} vidéo(s) dans la liste.")

    def choose_outdir(self) -> None:
        d = filedialog.askdirectory(title="Dossier de sortie")
        if d:
            self.outdir.set(d)

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _settings(self) -> Settings:
        return Settings(target_i=float(self.lufs.get()), target_tp=float(self.tp.get()),
                        target_lra=float(self.lra.get()),
                        overwrite=bool(self.overwrite.get()))

    def start(self) -> None:
        if self._busy():
            return
        if not self.files:
            messagebox.showinfo("Normaliser", "Ajoutez d'abord des vidéos.")
            return
        try:
            check_ffmpeg()
            settings = self._settings()
        except NormalizeError as exc:
            messagebox.showerror("ffmpeg introuvable", str(exc))
            return
        except (tk.TclError, ValueError):
            messagebox.showerror("Réglages", "Valeurs numériques invalides.")
            return

        outdir = Path(self.outdir.get()) if self.outdir.get().strip() else None
        for i in range(len(self.files)):
            self.tree.set(str(i), "avant", "")
            self.tree.set(str(i), "apres", "")
            self.tree.set(str(i), "statut", "En attente")
        self.stop_requested.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.worker = threading.Thread(
            target=self._work, args=(list(self.files), settings, outdir), daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_requested.set()
        self.status.set("Arrêt après la vidéo en cours…")

    # ------------------------------------------------------------ worker
    def _work(self, files: list[Path], settings: Settings, outdir: Path | None) -> None:
        total = len(files)
        outputs = plan_outputs(files, outdir)
        counts = {"ok": 0, "skip": 0, "err": 0}
        for i, f in enumerate(files):
            if self.stop_requested.is_set():
                break
            self.events.put(("row", i, None, None, "En cours…"))
            self.events.put(("status", f"[{i + 1}/{total}] {f.name}"))

            def prog(x: float, i=i) -> None:
                self.events.put(("progress", x, (i + x) / total))

            res = normalize(f, outputs[i], settings, progress=prog)
            before = f"{res.before.integrated:.1f} LUFS" if res.before else ""
            after = f"{res.after.integrated:.1f} LUFS" if res.after else ""
            if res.error:
                counts["err"] += 1
                status = "Erreur"
                self.events.put(("error", f.name, res.error))
            elif res.skipped:
                counts["skip"] += 1
                status = "Ignoré"
            else:
                counts["ok"] += 1
                status = "OK"
            self.events.put(("row", i, before, after, status))
            self.events.put(("progress", 1.0, (i + 1) / total))
        self.events.put(("done", counts))

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "progress":
                    self.pbar_file["value"] = ev[1]
                    self.pbar_all["value"] = ev[2]
                elif kind == "status":
                    self.status.set(ev[1])
                elif kind == "row":
                    _, i, before, after, status = ev
                    if before is not None:
                        self.tree.set(str(i), "avant", before)
                    if after is not None:
                        self.tree.set(str(i), "apres", after)
                    self.tree.set(str(i), "statut", status)
                elif kind == "error":
                    first = ev[2].strip().splitlines()[0] if ev[2].strip() else ev[2]
                    self.status.set(f"Erreur sur {ev[1]} : {first}")
                elif kind == "done":
                    c = ev[1]
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    msg = (f"{c['ok']} vidéo(s) normalisée(s), {c['skip']} ignorée(s), "
                           f"{c['err']} erreur(s).")
                    self.status.set("Terminé : " + msg)
                    messagebox.showinfo("Terminé", msg)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main(initial_paths: list[str] | None = None) -> int:
    app = App(initial_paths)
    app.mainloop()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
